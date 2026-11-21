//go:build darwin || linux

package main

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const coreHelperEnv = "VOICE_TEST_ENGINE_CORE_HELPER"

func TestEngineCoreHelper(t *testing.T) {
	if os.Getenv(coreHelperEnv) != "1" {
		return
	}
	switch os.Args[len(os.Args)-1] {
	case "--model-server":
		fakeModelServer()
	case "--session":
		fakeSessionProcess()
	}
	os.Exit(0)
}

func coreBase(env ...string) workerBase {
	return workerBase{Path: os.Args[0], Args: []string{os.Args[0], "-test.run=^TestEngineCoreHelper$", "--"},
		Env: append(append(os.Environ(), coreHelperEnv+"=1"), env...)}
}

func fakeModelServer() {
	fmt.Println(`{"model_server_ready":{"models_loaded":4,"accelerator":"cpu","probe_ms":1}}`)
	limit := 2
	fmt.Sscan(os.Getenv("FAKE_ADMIT"), &limit)
	var mu sync.Mutex
	held := map[int]bool{}
	next := 0
	for {
		buf, oob := make([]byte, 1), make([]byte, syscall.CmsgSpace(8*4))
		n, oobn, _, _, err := syscall.Recvmsg(3, buf, oob, 0)
		if err != nil || n == 0 {
			return
		}
		messages, _ := syscall.ParseSocketControlMessage(oob[:oobn])
		var fds []int
		for _, m := range messages {
			got, _ := syscall.ParseUnixRights(&m)
			fds = append(fds, got...)
		}
		if len(fds) != 2 || os.Getenv("FAKE_REFUSE") == "1" {
			for _, fd := range fds {
				syscall.Close(fd)
			}
			syscall.Write(3, []byte{'n'})
			continue
		}
		next++
		id := next
		in, out := os.NewFile(uintptr(fds[0]), "in"), os.NewFile(uintptr(fds[1]), "out")
		syscall.Write(3, []byte{'y'})
		go func() {
			defer func() { mu.Lock(); delete(held, id); mu.Unlock(); in.Close(); out.Close() }()
			for {
				var length [4]byte
				if _, err := io.ReadFull(in, length[:]); err != nil {
					return
				}
				rest := make([]byte, binary.NativeEndian.Uint32(length[:]))
				if _, err := io.ReadFull(in, rest); err != nil {
					return
				}
				body := []byte{0}
				mu.Lock()
				switch rest[0] {
				case frameAdmit:
					ok := held[id] || len(held) < limit
					if ok {
						held[id] = true
					}
					answer := make([]byte, 9)
					if ok {
						answer[0] = 1
					}
					retry := uint32(0)
					if !ok {
						retry = 1234
					}
					binary.NativeEndian.PutUint32(answer[1:], retry)
					binary.NativeEndian.PutUint32(answer[5:], uint32(len(held)))
					body = append(body, answer...)
				case frameRelease:
					delete(held, id)
				}
				mu.Unlock()
				reply := make([]byte, 4+frameHeader, 4+frameHeader+len(body))
				binary.NativeEndian.PutUint32(reply, uint32(frameHeader+len(body)))
				reply[4] = frameResult
				reply = append(reply, body...)
				if _, err := out.Write(reply); err != nil {
					return
				}
			}
		}()
	}
}

func fakeSessionProcess() {
	audio := make([]byte, 64)
	n, _ := syscall.Read(3, audio)
	kinds := map[int]string{}
	for fd := 3; fd < 256; fd++ {
		var st syscall.Stat_t
		if syscall.Fstat(fd, &st) != nil {
			continue
		}
		switch st.Mode & syscall.S_IFMT {
		case syscall.S_IFIFO:
			kinds[fd] = "fifo"
		case syscall.S_IFSOCK:
			kinds[fd] = "socket"
		default:
			kinds[fd] = "other"
		}
	}
	model := channelEnd{in: os.NewFile(6, "model-in"), out: os.NewFile(7, "model-out")}
	admitted, _, _, err := askAdmission(model, frameAdmit, 5*time.Second)
	said, _ := json.Marshal(map[string]any{"audio": string(audio[:n]), "fds": kinds, "admitted": admitted, "admit_error": fmt.Sprint(err),
		"env_engine": os.Getenv("AII_ENGINE_INTERNAL_FDS")})
	fmt.Println(string(said))
	syscall.Write(4, []byte("heard:"+string(audio[:n])))
	lines := bufio.NewScanner(os.Stdin)
	ended := make(chan struct{})
	go func() {
		var b [1]byte
		model.in.Read(b[:])
		close(ended)
	}()
	go func() {
		for lines.Scan() {
			fmt.Println("pong:" + lines.Text())
		}
	}()
	<-ended
	os.Exit(1)
}

type tenant struct {
	process  *sessionProcess
	toAudio  *os.File
	fromLine *bufio.Reader
	said     map[string]any
}

func openTenant(t *testing.T, core *engineCore, canary string) *tenant {
	t.Helper()
	pending, err := core.Admit()
	if err != nil {
		t.Fatalf("admit %s: %v", canary, err)
	}
	audioIn, toAudio, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	fromAudio, audioOut, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	process, err := pending.Start(audioIn, audioOut)
	if err != nil {
		t.Fatalf("start %s: %v", canary, err)
	}
	if _, err := toAudio.Write([]byte(canary)); err != nil {
		t.Fatal(err)
	}
	tn := &tenant{process: process, toAudio: toAudio, fromLine: bufio.NewReader(process.Stdout)}
	line, err := tn.fromLine.ReadString('\n')
	if err != nil {
		t.Fatalf("%s said nothing: %v", canary, err)
	}
	if err := json.Unmarshal([]byte(line), &tn.said); err != nil {
		t.Fatalf("%s: %q", canary, line)
	}
	heard := make([]byte, 64)
	n, _ := fromAudio.Read(heard)
	if got := string(heard[:n]); got != "heard:"+canary {
		t.Fatalf("%s's audio out carried %q", canary, got)
	}
	fromAudio.Close()
	return tn
}

func (tn *tenant) ping(t *testing.T, word string) string {
	t.Helper()
	if _, err := fmt.Fprintln(tn.process.Stdin, word); err != nil {
		return "write: " + err.Error()
	}
	got := make(chan string, 1)
	go func() { line, _ := tn.fromLine.ReadString('\n'); got <- strings.TrimSpace(line) }()
	select {
	case line := <-got:
		return line
	case <-time.After(3 * time.Second):
		return "no answer"
	}
}

func openDescriptors() int {
	n := 0
	for fd := 0; fd < 1024; fd++ {
		var st syscall.Stat_t
		if syscall.Fstat(fd, &st) == nil {
			n++
		}
	}
	return n
}

func TestEngineCoreTwoTenantsStayApart(t *testing.T) {
	core, err := newEngineCore(coreBase("FAKE_ADMIT=2", internalFDsEnv+"=9,10"), 10*time.Second, 2*time.Second, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer core.Stop(2 * time.Second)
	a := openTenant(t, core, "canary-AAAA")
	b := openTenant(t, core, "canary-BBBB")
	for name, tn := range map[string]*tenant{"A": a, "B": b} {
		other := map[string]string{"A": "canary-BBBB", "B": "canary-AAAA"}[name]
		if got := tn.said["audio"]; got != map[string]string{"A": "canary-AAAA", "B": "canary-BBBB"}[name] || strings.Contains(fmt.Sprint(tn.said), other) {
			t.Errorf("%s's session process was given %v", name, tn.said)
		}
		fds := tn.said["fds"].(map[string]any)
		fifos, sockets := 0, 0
		for _, kind := range fds {
			switch kind {
			case "fifo":
				fifos++
			case "socket":
				sockets++
			}
		}

		if fifos != 5 || sockets != 0 {
			t.Errorf("%s's session process holds %d pipes and %d sockets: %v", name, fifos, sockets, fds)
		}
		if tn.said["admitted"] != true || tn.said["env_engine"] != "" {
			t.Errorf("%s: the worker's own Admit on its channel was %v (%v); engine ends named %q", name, tn.said["admitted"], tn.said["admit_error"], tn.said["env_engine"])
		}
	}

	if _, err := core.Admit(); !errors.As(err, new(engineBusy)) || err.(engineBusy).RetryAfter != 1234*time.Millisecond || err.(engineBusy).Held != 2 {
		t.Fatalf("a third channel past the admission: %v", err)
	}

	a.process.Kill()
	<-a.process.Done()
	if got := b.ping(t, "after-A"); got != "pong:after-A" {
		t.Fatalf("B after A's end: %q", got)
	}
	time.Sleep(100 * time.Millisecond)
	c := openTenant(t, core, "canary-CCCC")
	if core.Live() != 2 {
		t.Errorf("live session processes: %d", core.Live())
	}

	core.server.control.kill(core.server.cmd)
	select {
	case <-core.Lost():
	case <-time.After(5 * time.Second):
		t.Fatal("the server's end was not seen")
	}
	for name, tn := range map[string]*tenant{"B": b, "C": c} {
		select {
		case <-tn.process.Done():
		case <-time.After(5 * time.Second):
			t.Fatalf("%s outlived the model server", name)
		}
	}
	if _, err := core.Admit(); !errors.Is(err, errModelServerLost) {
		t.Errorf("a channel after the server's end: %v", err)
	}
}

func TestEngineCoreRefusalsAndLeaks(t *testing.T) {
	refusing, err := newEngineCore(coreBase("FAKE_REFUSE=1"), 10*time.Second, 2*time.Second, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := refusing.Admit(); err == nil || !strings.Contains(err.Error(), "refused the channel") {
		t.Errorf("a server that refuses its channel: %v", err)
	}
	refusing.Stop(time.Second)

	core, err := newEngineCore(coreBase("FAKE_ADMIT=1"), 10*time.Second, 2*time.Second, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer core.Stop(time.Second)
	before := openDescriptors()
	for i := 0; i < 100; i++ {
		pending, err := core.Admit()
		for tries := 0; errors.As(err, new(engineBusy)) && tries < 100; tries++ {
			time.Sleep(5 * time.Millisecond)
			pending, err = core.Admit()
		}
		if err != nil {
			t.Fatalf("open %d: %v", i, err)
		}
		pending.Cancel()
	}
	if after := openDescriptors(); after != before {
		t.Errorf("descriptors after 100 opens and closes: %d, before %d", after, before)
	}
}
