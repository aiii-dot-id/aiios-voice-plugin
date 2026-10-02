//go:build darwin || linux

package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// The carrier composed with the native worker's own code: the model-free
// fixture worker (runtime/native/session built with -DAII_WORKER_FIXTURE=ON)
// that AII_NATIVE_INTERRUPT_FIXTURE names, as for the repository's other
// native fixture tests. One test-only relay stands between them: the fixture
// reports accelerator "fixture", which readinessReport refuses, so the relay
// rewrites that one line to the deterministic lane. Every other private line
// passes through, and the worker owns the audio and liveness descriptors.
func TestNativeFixtureRelayHelper(t *testing.T) {
	if os.Getenv("AII_VOICE_TEST_NATIVE_RELAY") != "1" {
		return
	}
	cmd := exec.Command(os.Getenv("AII_VOICE_TEST_NATIVE_FIXTURE"), "fixture", "fixture", "fixture", "fixture", "fixture", "fixture", "fixture")
	cmd.Stdin, cmd.Stderr = os.Stdin, os.Stderr
	cmd.ExtraFiles = []*os.File{os.NewFile(3, "audio-in"), os.NewFile(4, "audio-out"), os.NewFile(5, "carrier-liveness")}
	out, err := cmd.StdoutPipe()
	if err != nil || cmd.Start() != nil {
		os.Exit(80)
	}
	for _, f := range cmd.ExtraFiles {
		_ = f.Close()
	}
	lines := bufio.NewReaderSize(out, 1<<20)
	if first, err := lines.ReadBytes('\n'); err != nil || !bytes.Contains(first, []byte(`"backend":"fixture-native"`)) {
		os.Exit(81)
	}
	fmt.Println(`{"ready":{"identity":{"backend":"deterministic-test-not-real-model"}}}`)
	_, _ = io.Copy(os.Stdout, lines)
	if err := cmd.Wait(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) && exit.ExitCode() > 0 {
			os.Exit(exit.ExitCode())
		}
		os.Exit(82)
	}
	os.Exit(0)
}

type nativeHost struct {
	t        *testing.T
	wmu      sync.Mutex
	host     io.WriteCloser
	audioIn  *os.File // held open: a closed input would end the session
	audioOut *os.File
	stderr   bytes.Buffer // read only after done
	done     chan struct{}
	err      error
	laneEnd  chan struct{}
	mu       sync.Mutex
	replies  map[int]json.RawMessage
	events   []json.RawMessage
}

func startNativeCarrier(t *testing.T, fixture string, env ...string) *nativeHost {
	t.Helper()
	carrier := filepath.Join(t.TempDir(), "carrier")
	if output, err := exec.Command("go", "build", "-o", carrier, ".").CombinedOutput(); err != nil {
		t.Fatalf("build carrier: %v: %s", err, output)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	var pipes [3][2]*os.File // audio in, audio out, public: {read, write}
	for i := range pipes {
		if pipes[i][0], pipes[i][1], err = os.Pipe(); err != nil {
			t.Fatal(err)
		}
	}
	h := &nativeHost{t: t, audioIn: pipes[0][1], audioOut: pipes[1][0], done: make(chan struct{}),
		laneEnd: make(chan struct{}), replies: map[int]json.RawMessage{}}
	cmd := exec.Command(carrier, self, "-test.run=^TestNativeFixtureRelayHelper$")
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AII_") && !strings.HasPrefix(kv, aiiosdk.DescribeEnv+"=") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, "AII_VOICE_TEST_NATIVE_RELAY=1", "AII_VOICE_TEST_NATIVE_FIXTURE="+fixture,
		"AII_AUDIO_IN_FD=3", "AII_AUDIO_OUT_FD=4")
	cmd.Env = append(cmd.Env, env...)
	cmd.ExtraFiles = []*os.File{pipes[0][0], pipes[1][1]}
	cmd.Stdout = pipes[2][1]
	cmd.Stderr = &h.stderr
	cmd.WaitDelay = 5 * time.Second
	if h.host, err = cmd.StdinPipe(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	for _, f := range []*os.File{pipes[0][0], pipes[1][1], pipes[2][1]} {
		_ = f.Close()
	}
	go func() { h.err = cmd.Wait(); close(h.done) }()
	go h.read(pipes[2][0])
	t.Cleanup(func() {
		_ = h.host.Close()
		_ = h.audioIn.Close()
		select {
		case <-h.done:
		case <-time.After(15 * time.Second):
			_ = cmd.Process.Kill()
			<-h.done
		}
		_ = pipes[2][0].Close()
		_ = h.audioOut.Close()
	})
	return h
}

func (h *nativeHost) write(frame string) {
	h.wmu.Lock()
	defer h.wmu.Unlock()
	if err := aiiosdk.WriteFrame(h.host, []byte(frame), aiiosdk.MaxControlFrameBytes); err != nil {
		h.t.Errorf("host write: %v", err)
	}
}

func (h *nativeHost) control(id int, op string, args string) {
	h.write(fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"invoke.call","params":{"operation":%q,"arguments":%s}}`, id, op, args))
}

// read is the host's session client: replies, events, and the one host call
// the worker makes here (its settings).
func (h *nativeHost) read(public *os.File) {
	defer close(h.laneEnd)
	for {
		frame, err := aiiosdk.ReadFrame(public, aiiosdk.MaxServerFrameBytes)
		if err != nil {
			return
		}
		var m struct {
			ID     json.RawMessage `json:"id"`
			Method string          `json:"method"`
			Result json.RawMessage `json:"result"`
			Error  json.RawMessage `json:"error"`
			Params json.RawMessage `json:"params"`
		}
		if json.Unmarshal(frame, &m) != nil {
			continue
		}
		switch {
		case m.Method == "invoke.call" && len(m.ID) != 0:
			if bytes.Contains(m.Params, []byte(`"settings.get"`)) {
				h.write(fmt.Sprintf(`{"jsonrpc":"2.0","id":%s,"result":{"status":"succeeded","operation_result":{"values":{"turn_pause_ms":768}}}}`, m.ID))
			} else {
				h.write(fmt.Sprintf(`{"jsonrpc":"2.0","id":%s,"error":{"code":-32601,"message":"not offered by this test host"}}`, m.ID))
			}
		case m.Method != "" && len(m.ID) == 0:
			h.mu.Lock()
			h.events = append(h.events, m.Params)
			h.mu.Unlock()
		case m.Method == "" && len(m.ID) != 0:
			var id int
			if json.Unmarshal(m.ID, &id) == nil {
				h.mu.Lock()
				h.replies[id] = append(m.Result, m.Error...)
				h.mu.Unlock()
			}
		}
	}
}

// await polls what the host has observed; every wait is on something the
// carrier or the worker actually did.
func (h *nativeHost) await(what string, ok func() bool) {
	h.t.Helper()
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(time.Millisecond) {
		h.mu.Lock()
		done := ok()
		h.mu.Unlock()
		if done {
			return
		}
		if time.Now().After(deadline) {
			h.t.Fatalf("%s: not observed", what)
		}
	}
}

func (h *nativeHost) event(kind string) json.RawMessage {
	h.t.Helper()
	var found json.RawMessage
	h.await("event "+kind, func() bool {
		for _, e := range h.events {
			if bytes.Contains(e, []byte(`"type":"`+kind+`"`)) {
				found = e
				return true
			}
		}
		return false
	})
	return found
}

// A host that stops reading audio leaves the worker's next frame, larger than
// the pipe, part-written until its write deadline. Whichever worker thread
// sees that deadline, the worker retires: the carrier exits through its fault
// path, the host's lane ends, and no byte follows the torn frame. The fixture
// seam makes the audio writer's own check the one that sees it.
func TestUnframedAudioWriteRetiresWorkerAndCarrier(t *testing.T) {
	fixture := os.Getenv("AII_NATIVE_INTERRUPT_FIXTURE")
	if fixture == "" {
		t.Skip("AII_NATIVE_INTERRUPT_FIXTURE names no aii_voice_worker_fixture (-DAII_WORKER_FIXTURE=ON)")
	}
	h := startNativeCarrier(t, fixture, "AII_FIXTURE_AUDIO_DEADLINE=writer")
	h.control(1, aiiosdk.OpSessionOpen, `{"session_id":"torn","output_handle":"playback","audio":{"format":"s16le","input":null,"output":{"rate":48000,"channels":2}}}`)
	h.event("session_ready")
	h.control(2, aiiosdk.OpSessionSynthesize, `{"session_id":"torn","synthesis_id":"reply","text":"Flood."}`)
	h.await("synthesis admission", func() bool { return h.replies[2] != nil })
	var head [28]byte
	if _, err := io.ReadFull(h.audioOut, head[:]); err != nil || string(head[:4]) != "AUD1" {
		t.Fatalf("first audio frame: %v %q", err, head[:4])
	}
	if _, err := io.ReadFull(h.audioOut, make([]byte, binary.BigEndian.Uint32(head[24:28]))); err != nil {
		t.Fatal(err)
	} // the host takes one whole frame, then stops reading
	failure := h.event("failure")
	var reason struct {
		Reason   string `json:"reason"`
		Released bool   `json:"resources_released"`
	}
	if json.Unmarshal(failure, &reason) != nil || reason.Reason != "native pipe write interrupted/expired" || !reason.Released {
		t.Fatalf("not the audio writer's own deadline: %s", failure)
	}
	select {
	case <-h.done:
	case <-time.After(6 * time.Second):
		t.Fatalf("the carrier stayed up after the failure: its worker kept an audio pipe holding part of a frame")
	}
	var exit *exec.ExitError
	if !errors.As(h.err, &exit) || exit.ExitCode() != 1 || !strings.Contains(h.stderr.String(), "aii-voice-t3: EOF") {
		t.Fatalf("carrier exit %v without its worker-exit fault:\n%s", h.err, h.stderr.String())
	}
	select {
	case <-h.laneEnd:
	case <-time.After(5 * time.Second):
		t.Fatal("the host's lane did not end with the carrier")
	}
	rest, err := io.ReadAll(h.audioOut) // the worker's end has closed
	if err != nil || len(rest) < 28 || string(rest[:4]) != "AUD1" || len(rest)-28 >= int(binary.BigEndian.Uint32(rest[24:28])) {
		t.Fatalf("after the first frame the pipe held %d byte(s) (%v), want exactly the frame the expired write began", len(rest), err)
	}
	t.Logf("%s; carrier %v; then %d of %d bytes of the next frame and the end of the pipe", reason.Reason, h.err, len(rest), 28+binary.BigEndian.Uint32(rest[24:28]))
}
