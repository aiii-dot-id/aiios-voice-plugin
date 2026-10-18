//go:build !windows

package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestCarrierWorkerHelper(t *testing.T) {
	if os.Getenv("AII_VOICE_TEST_WORKER") != "1" {
		return
	}
	report := os.NewFile(4, "audio-out")
	fmt.Println(`{"ready":{"identity":{"backend":"deterministic-test-not-real-model"}}}`)
	var owed struct {
		sync.Mutex
		lines []string
	}
	go func() {
		requests := bufio.NewScanner(os.Stdin)
		for requests.Scan() {
			var req privateRequest
			if json.Unmarshal(requests.Bytes(), &req) != nil {
				os.Exit(70)
			}
			fmt.Fprintf(report, "request %d %s\n", req.ID, req.Operation)
		}

		owed.Lock()
		for _, line := range owed.lines {
			fmt.Println(line)
		}
		os.Exit(0)
	}()
	steps := bufio.NewScanner(os.NewFile(3, "audio-in"))
	for steps.Scan() {
		step, arg, _ := strings.Cut(steps.Text(), " ")
		switch step {
		case "write":
			fmt.Println(arg)
		case "atclose":
			owed.Lock()
			owed.lines = append(owed.lines, arg)
			owed.Unlock()
			fmt.Fprintln(report, "owed")
		case "exit":
			code, _ := strconv.Atoi(arg)
			os.Exit(code)
		}
	}
	os.Exit(71)
}

type carrierProcess struct {
	host       io.WriteCloser
	public     *os.File
	steps      *os.File
	reportFile *os.File
	report     *bufio.Reader
	stderr     bytes.Buffer
	done       chan struct{}
	err        error
}

func startCarrier(t *testing.T) *carrierProcess {
	t.Helper()
	carrier := filepath.Join(t.TempDir(), "carrier")
	if output, err := exec.Command("go", "build", "-o", carrier, ".").CombinedOutput(); err != nil {
		t.Fatalf("build carrier: %v: %s", err, output)
	}
	worker, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	var pipes [3][2]*os.File
	for i := range pipes {
		if pipes[i][0], pipes[i][1], err = os.Pipe(); err != nil {
			t.Fatal(err)
		}
	}
	p := &carrierProcess{public: pipes[2][0], steps: pipes[0][1], reportFile: pipes[1][0],
		report: bufio.NewReader(pipes[1][0]), done: make(chan struct{})}
	cmd := exec.Command(carrier, worker, "-test.run=^TestCarrierWorkerHelper$")

	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AII_") && !strings.HasPrefix(kv, aiiosdk.DescribeEnv+"=") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, "AII_VOICE_TEST_WORKER=1", "AII_AUDIO_IN_FD=3", "AII_AUDIO_OUT_FD=4")
	cmd.ExtraFiles = []*os.File{pipes[0][0], pipes[1][1]}
	cmd.Stdout = pipes[2][1]
	cmd.Stderr = &p.stderr
	cmd.WaitDelay = 5 * time.Second
	if p.host, err = cmd.StdinPipe(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	for _, f := range []*os.File{pipes[0][0], pipes[1][1], pipes[2][1]} {
		_ = f.Close()
	}
	go func() { p.err = cmd.Wait(); close(p.done) }()
	t.Cleanup(func() {
		_ = p.steps.Close()
		_ = p.host.Close()
		select {
		case <-p.done:
		case <-time.After(10 * time.Second):
			_ = cmd.Process.Kill()
			<-p.done
		}
		_ = p.public.Close()
		_ = p.reportFile.Close()
	})
	return p
}

func (p *carrierProcess) control(t *testing.T, id int, op, args string) {
	t.Helper()
	frame := fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"invoke.call","params":{"operation":%q,"arguments":%s}}`, id, op, args)
	if err := aiiosdk.WriteFrame(p.host, []byte(frame), aiiosdk.MaxControlFrameBytes); err != nil {
		t.Fatal(err)
	}
}

func (p *carrierProcess) step(t *testing.T, step string) {
	t.Helper()
	if _, err := io.WriteString(p.steps, step+"\n"); err != nil {
		t.Fatal(err)
	}
}

func (p *carrierProcess) expect(t *testing.T, want string) {
	t.Helper()
	_ = p.reportFile.SetReadDeadline(time.Now().Add(10 * time.Second))
	if line, err := p.report.ReadString('\n'); err != nil || strings.TrimSuffix(line, "\n") != want {
		t.Fatalf("worker reported %q (%v), want %q", line, err, want)
	}
}

func (p *carrierProcess) exit(t *testing.T) error {
	t.Helper()
	select {
	case <-p.done:
		return p.err
	case <-time.After(8 * time.Second):
		t.Fatal("carrier stayed alive with host stdin open")
		return nil
	}
}

func hostVerdict(t *testing.T, public *os.File, id string) (string, []json.RawMessage) {
	t.Helper()
	_ = public.SetReadDeadline(time.Now().Add(10 * time.Second))
	var events []json.RawMessage
	for {
		frame, err := aiiosdk.ReadFrame(public, aiiosdk.MaxServerFrameBytes)
		if errors.Is(err, os.ErrDeadlineExceeded) {
			t.Fatal("the host saw neither a reply nor the end of the lane")
		}
		if err != nil {
			return "admission unknown", events
		}
		var m struct {
			ID     json.RawMessage `json:"id"`
			Method json.RawMessage `json:"method"`
			Error  json.RawMessage `json:"error"`
			Params json.RawMessage `json:"params"`
		}
		if json.Unmarshal(frame, &m) != nil {
			continue
		}
		switch {
		case len(m.Method) == 0 && string(m.ID) == id:
			if len(m.Error) != 0 {
				return "refused", events
			}
			return "admitted", events
		case len(m.Method) != 0 && len(m.ID) == 0:
			events = append(events, m.Params)
		}
	}
}

func TestCarrierExitsWhenWorkerDiesWhileSDKReaderIsIdle(t *testing.T) {
	p := startCarrier(t)

	p.control(t, 1, aiiosdk.OpSessionStatus, `{}`)
	p.expect(t, "request 1 "+aiiosdk.OpSessionStatus)
	p.step(t, `write {"id":1,"result":{"state":"idle"}}`)
	if verdict, _ := hostVerdict(t, p.public, "1"); verdict != "admitted" {
		t.Fatalf("status round trip: %s", verdict)
	}
	p.step(t, "exit 3")
	err := p.exit(t)
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 1 {
		t.Fatalf("worker crash did not end the carrier as a failure: %v", err)
	}

	stderr := p.stderr.String()
	for _, want := range []string{`"phase":"worker-readiness-validated"`, "aii-voice-t3: EOF", "exit status 3"} {
		if !strings.Contains(stderr, want) {
			t.Fatalf("carrier retirement lacks %q:\n%s", want, stderr)
		}
	}
	if _, err := aiiosdk.ReadFrame(p.public, aiiosdk.MaxServerFrameBytes); err != io.EOF {
		t.Fatalf("public lane did not end with the carrier: %v", err)
	}
}

func TestCarrierLeavesDeliveredControlUnknownWhenPrivateLaneFails(t *testing.T) {
	for _, failure := range []struct{ name, step, fault string }{
		{"worker death", "exit 3", "aii-voice-t3: EOF"},
		{"admission deadline", "", "aii-voice-t3: worker admission timeout"},
	} {
		t.Run(failure.name, func(t *testing.T) {
			p := startCarrier(t)
			p.control(t, 7, aiiosdk.OpSessionOpen, `{"session_id":"vdr-02"}`)
			p.expect(t, "request 1 "+aiiosdk.OpSessionOpen)
			if failure.step != "" {
				p.step(t, failure.step)
			}
			if verdict, _ := hostVerdict(t, p.public, "7"); verdict != "admission unknown" {
				t.Fatalf("the host would conclude %q for a control the worker received and never answered", verdict)
			}
			if err := p.exit(t); err == nil || !strings.Contains(p.stderr.String(), failure.fault) {
				t.Fatalf("carrier exit %v without the private fault %q:\n%s", err, failure.fault, p.stderr.String())
			}
		})
	}
}

func TestCarrierForwardsWorkerRefusalAsRefusal(t *testing.T) {
	p := startCarrier(t)
	p.control(t, 7, aiiosdk.OpSessionOpen, `{"session_id":"vdr-02"}`)
	p.expect(t, "request 1 "+aiiosdk.OpSessionOpen)
	p.step(t, `write {"id":1,"error":"SESSION_REFUSED"}`)
	if verdict, _ := hostVerdict(t, p.public, "7"); verdict != "refused" {
		t.Fatalf("the worker's refusal reached the host as %q", verdict)
	}
	_ = p.host.Close()
	if err := p.exit(t); err != nil {
		t.Fatalf("host close after a refusal: %v\n%s", err, p.stderr.String())
	}
}

type slowHost struct{ r io.Reader }

func (h slowHost) Read(b []byte) (int, error) {
	if len(b) > 16<<10 {
		b = b[:16<<10]
	}
	time.Sleep(time.Millisecond)
	return h.r.Read(b)
}

func finalBurst(t *testing.T, p *carrierProcess, burst int, host func(io.Reader) io.Reader) []int {
	t.Helper()
	p.control(t, 1, aiiosdk.OpSessionStatus, `{}`)
	p.expect(t, "request 1 "+aiiosdk.OpSessionStatus)
	p.step(t, `write {"id":1,"result":{"state":"idle"}}`)
	if verdict, _ := hostVerdict(t, p.public, "1"); verdict != "admitted" {
		t.Fatalf("status round trip: %s", verdict)
	}
	_ = p.public.SetReadDeadline(time.Now().Add(20 * time.Second))
	received := make(chan []int, 1)
	go func() {
		var got []int
		public := host(p.public)
		for {
			frame, err := aiiosdk.ReadFrame(public, aiiosdk.MaxServerFrameBytes)
			if err != nil {
				received <- got
				return
			}
			var m struct {
				Params struct {
					Sequence int `json:"sequence"`
				} `json:"params"`
			}
			if json.Unmarshal(frame, &m) == nil {
				got = append(got, m.Params.Sequence)
			}
		}
	}()
	pad := strings.Repeat("x", 32000)
	var steps strings.Builder
	for i := range burst {
		fmt.Fprintf(&steps, "write {\"event\":{\"type\":\"resources_released\",\"sequence\":%d,\"pad\":%q}}\n", i, pad)
	}
	steps.WriteString("exit 3")
	p.step(t, steps.String())
	return <-received
}

func TestFinalWorkerEventsReachASlowHost(t *testing.T) {
	for _, burst := range []int{8, 40, 100} {
		t.Run(strconv.Itoa(burst), func(t *testing.T) {
			p := startCarrier(t)
			got := finalBurst(t, p, burst, func(r io.Reader) io.Reader { return slowHost{r} })
			if err := p.exit(t); err == nil {
				t.Fatal("worker crash became a successful carrier exit")
			}
			stderr := p.stderr.String()
			if !strings.Contains(stderr, "aii-voice-t3: EOF") || strings.Contains(stderr, "unproven") {
				t.Fatalf("carrier retirement:\n%s", stderr)
			}
			for i, sequence := range got {
				if sequence != i {
					t.Fatalf("events out of order at %d: %v", i, got)
				}
			}
			if len(got) != burst {
				t.Fatalf("a slow host received %d of the %d final events", len(got), burst)
			}
		})
	}
}

func TestFinalWorkerEventsToAStalledHostAreUnproven(t *testing.T) {
	p := startCarrier(t)
	finalBurst(t, p, 8, func(r io.Reader) io.Reader {
		select {
		case <-p.done:
		case <-time.After(10 * time.Second):
		}
		return r
	})
	if err := p.exit(t); err == nil || !strings.Contains(p.stderr.String(), "worker event delivery unproven") {
		t.Fatalf("carrier exit %v without reporting unproven delivery:\n%s", err, p.stderr.String())
	}
}

func lateReplyTail(t *testing.T, padding int) *carrierProcess {
	t.Helper()
	p := startCarrier(t)
	owe := func(line string) {
		p.step(t, "atclose "+line)
		p.expect(t, "owed")
	}
	owe(`{"id":1,"result":{"state":"open"}}`)
	pad := strings.Repeat("x", 32000)
	for i := range padding {
		owe(fmt.Sprintf(`{"event":{"type":"progress","sequence":%d,"pad":%q}}`, i, pad))
	}
	owe(fmt.Sprintf(`{"event":{"type":"failure","sequence":%d,"scope":"engine_resources","reason":"session aborted","resources_released":true,"playback_verified":false}}`, padding))
	p.control(t, 7, aiiosdk.OpSessionOpen, `{"session_id":"late-reply"}`)
	p.expect(t, "request 1 "+aiiosdk.OpSessionOpen)
	return p
}

func TestTerminalEventBehindALateReplyIsDeliveredOrUnproven(t *testing.T) {
	t.Run("healthy host", func(t *testing.T) {
		p := lateReplyTail(t, 0)
		verdict, events := hostVerdict(t, p.public, "7")
		err := p.exit(t)
		stderr := p.stderr.String()
		if verdict != "admission unknown" {
			t.Fatalf("a reply after the deadline reached the host as %q", verdict)
		}
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 1 || !strings.Contains(stderr, "aii-voice-t3: worker admission timeout") {
			t.Fatalf("carrier exit %v without the admission deadline fault:\n%s", err, stderr)
		}
		if strings.Contains(stderr, "unproven") || strings.Contains(stderr, "not delivered") {
			t.Fatalf("a healthy host's delivery was not proven:\n%s", stderr)
		}
		var terminal struct {
			Type     string `json:"type"`
			Released bool   `json:"resources_released"`
		}
		if len(events) != 1 || json.Unmarshal(events[0], &terminal) != nil || terminal.Type != "failure" || !terminal.Released {
			t.Fatalf("the host received %d event(s) %s, want the worker's terminal resource release; carrier:\n%s", len(events), events, stderr)
		}
	})
	t.Run("stalled host", func(t *testing.T) {
		p := lateReplyTail(t, 8)
		err := p.exit(t)
		stderr := p.stderr.String()
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 1 || !strings.Contains(stderr, "aii-voice-t3: worker admission timeout") {
			t.Fatalf("carrier exit %v without the admission deadline fault:\n%s", err, stderr)
		}
		if !strings.Contains(stderr, "worker event delivery unproven") {
			t.Fatalf("events the host never took were not reported unproven:\n%s", stderr)
		}
		if verdict, _ := hostVerdict(t, p.public, "7"); verdict != "admission unknown" {
			t.Fatalf("a reply after the deadline reached the host as %q", verdict)
		}
	})
}
