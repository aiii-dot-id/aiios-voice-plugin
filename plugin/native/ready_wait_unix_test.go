//go:build darwin || linux

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// This compiled test process is also a worker that never reports ready. As
// "closes" it takes its input until its carrier closes it, says nothing and
// ends. As "stuck" it does not look at its input at all, as a worker that is
// still loading its models does not. The carrier starts it as the bound
// worker of a packaged runtime, as it starts the launch fixture of
// native_runtime_test.go.
func init() {
	if !strings.HasPrefix(filepath.Base(os.Args[0]), "launch-fixture") {
		return
	}
	switch os.Getenv("AII_VOICE_TEST_SILENT_WORKER") {
	case "closes":
		_, _ = io.Copy(io.Discard, os.Stdin)
		os.Exit(0)
	case "stuck":
		time.Sleep(time.Minute)
		os.Exit(0)
	}
}

// carrierLog keeps what a carrier and its worker wrote on their standard
// error, and how long after the carrier's start one line of it was first
// there. A log read only when the carrier has ended does not show when a
// thing was said.
type carrierLog struct {
	began time.Time
	text  bytes.Buffer
	line  string
	at    time.Duration
}

func (l *carrierLog) Write(p []byte) (int, error) {
	n, err := l.text.Write(p)
	if l.at == 0 && strings.Contains(l.text.String(), l.line) {
		l.at = time.Since(l.began)
	}
	return n, err
}

func (l *carrierLog) String() string { return l.text.String() }

// startupElapsed is the elapsed_ms of the first start-up record of a phase in
// a carrier's log: the carrier's own count, from the instant its process
// initialised.
func startupElapsed(t *testing.T, log, phase string) time.Duration {
	t.Helper()
	for _, line := range strings.Split(log, "\n") {
		var r struct {
			Component string `json:"component"`
			Phase     string `json:"phase"`
			ElapsedMS int64  `json:"elapsed_ms"`
		}
		if json.Unmarshal([]byte(line), &r) == nil && r.Component == "voice-carrier-startup" && r.Phase == phase {
			return time.Duration(r.ElapsedMS) * time.Millisecond
		}
	}
	t.Fatalf("the carrier's log has no start-up record of %s:\n%s", phase, log)
	return 0
}

// silentCarrier builds the real carrier bound to the runtime at root under
// this profile, runs it over this test process as a worker that never
// reports ready, with the host's lane held open so that nothing ends it from
// outside, and returns its log, how long it ran and how it ended. The log
// notes when line was first in it.
func silentCarrier(t *testing.T, root string, p runtimeProfile, worker, line string) (*carrierLog, time.Duration, error) {
	t.Helper()
	digest := writeRuntimeProfile(t, root, p)
	carrier := filepath.Join(root, "carrier")
	if err := os.Remove(carrier); err != nil { // the fixture's stand-in for a carrier, or the one built for another profile
		t.Fatal(err)
	}
	if output, err := exec.Command("go", "build", "-ldflags", "-X main.packagedRuntimeSHA="+digest, "-o", carrier, ".").CombinedOutput(); err != nil {
		t.Fatalf("build the bound carrier: %v: %s", err, output)
	}
	var pipes [2][2]*os.File // audio in, audio out: {read, write}
	for i := range pipes {
		var err error
		if pipes[i][0], pipes[i][1], err = os.Pipe(); err != nil {
			t.Fatal(err)
		}
		defer pipes[i][0].Close()
		defer pipes[i][1].Close()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second) // a carrier that waits by no table is ended here
	defer cancel()
	cmd := exec.CommandContext(ctx, carrier)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AII_") && !strings.HasPrefix(kv, aiiosdk.DescribeEnv+"=") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, "AII_MODELS_DIR="+t.TempDir(), "AII_AUDIO_IN_FD=3", "AII_AUDIO_OUT_FD=4", "AII_VOICE_TEST_SILENT_WORKER="+worker)
	cmd.ExtraFiles = []*os.File{pipes[0][0], pipes[1][1]}
	log := &carrierLog{line: line}
	cmd.Stderr = log             // the worker's too: reading it to its end waits for the worker to have gone
	host, err := cmd.StdinPipe() // the host's lane, held open
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	log.began = time.Now()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	err = cmd.Wait()
	return log, time.Since(log.began), err
}

// notReady is what a carrier says of a worker that did not report ready,
// under a table that gives that this many milliseconds: up to the number it
// gives last, of when the worker was started.
func notReady(ms int64) string {
	return "aii-voice-t3: the worker did not report ready within " + strconv.FormatInt(ms, 10) + " ms of this carrier's start, the time the limits table gives it (ready_ms); the worker was started "
}

// A PACKAGED CARRIER WAITS FOR ITS WORKER'S READINESS THE TIME ITS SIGNED
// PROFILE STATES, AND SAYS SO AS IT PASSES. The carrier is built bound to a
// runtime whose profile gives readiness one second, the least the table
// allows, and whose worker never reports. It gives up a second after its
// start, not its default's time after: its log says the worker did not
// report ready, in how long, counted from what, and which member of the
// table that is, and it exits with status 1 without having opened the host's
// lane.
//
// A worker that ends when its input is closed is not killed. One that does
// not, as a worker still loading its models does not, is waited for the
// profile's worker_exit_ms, stated here as a second and a half, and then
// killed with its process group; the log names that member and its number
// too. The sentence about readiness is in the log before that wait, not after
// it, when the allowance a set declares to the host for its start may have
// passed.
func TestAPackagedCarrierSaysItsWorkerDidNotReportReadyInTheProfilesTime(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	worker, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	const exit = 1500 * time.Millisecond
	const forced = "worker required forced cleanup: it did not exit in 1500 ms after its input was closed, the time the limits table gives it (worker_exit_ms)"
	for _, tc := range []struct {
		worker      string
		least, most time.Duration
		killed      bool
	}{
		{"closes", readyFloor, readyFloor + exit, false},
		{"stuck", readyFloor + exit, readyFloor + exit + 3*time.Second, true},
	} {
		t.Run(tc.worker, func(t *testing.T) {
			root, p := nativeRuntimeFixture(t, worker)
			p.Limits = &profileLimits{ReadyMS: ms(readyFloor.Milliseconds()), WarmProbeMS: ms(250), RetireMS: ms(250), WorkerExitMS: ms(exit.Milliseconds())}
			log, waited, err := silentCarrier(t, root, p, tc.worker, notReady(readyFloor.Milliseconds()))

			var status *exec.ExitError
			if !errors.As(err, &status) || status.ExitCode() != 1 {
				t.Fatalf("the carrier did not end with status 1 (%v) after %v:\n%s", err, waited, log.String())
			}
			wanted, never := []string{log.line, `"phase":"worker-readiness-not-reported"`}, []string{`"phase":"worker-readiness-validated"`, "AII_VOICE_READY", "worker startup timeout"}
			if tc.killed {
				wanted = append(wanted, forced)
			} else {
				never = append(never, "worker required forced cleanup", "signal: killed")
			}
			for _, want := range wanted {
				if !strings.Contains(log.String(), want) {
					t.Errorf("the carrier's log does not say %q:\n%s", want, log.String())
				}
			}
			for _, not := range never {
				if strings.Contains(log.String(), not) {
					t.Errorf("the carrier's log says %q:\n%s", not, log.String())
				}
			}
			if waited < tc.least || waited > tc.most {
				t.Errorf("the profile gives readiness %v and a worker's exit %v, and the carrier ended after %v; it should be %v to %v", readyFloor, exit, waited, tc.least, tc.most)
			}
			// Said as the wait passed, which for the worker that has to be
			// killed is the whole wait for its exit before the carrier ends.
			if log.at < readyFloor || (tc.killed && waited-log.at < exit-500*time.Millisecond) {
				t.Errorf("the carrier's log said readiness had passed %v after the start and the carrier ended after %v; it should say so as the wait passes, before it ends its worker", log.at, waited)
			}
			lines := strings.Split(strings.TrimSpace(log.String()), "\n")
			t.Logf("under a profile that gives readiness %v the carrier said it had passed %v after its start and ended after %v; its log ends: %q",
				readyFloor, log.at.Round(time.Millisecond), waited.Round(time.Millisecond), lines[max(0, len(lines)-3):])
		})
	}
}

// THE WAIT FOR READINESS IS COUNTED FROM THE CARRIER'S OWN START, SO THE
// CHECK OF ITS RUNTIME'S FILES IS INSIDE IT. The runtime here has a file of
// two gibibytes, so that checking it takes a time the carrier's own start-up
// records show. Counted from the worker's start, as the wait was, that time
// came on top of ready_ms and the host's allowance for the start could pass
// first.
//
// Under a profile that gives readiness twice what the check takes, the
// carrier says its worker did not report ready when ready_ms have passed
// since its own start, not that long after the check. Under a profile that
// gives one second, the least the table allows, the check leaves the worker
// little of it, or none where the check itself takes a second or more: the
// carrier gives up when the second has passed, or as soon as it has started
// its worker where it had passed already, and never a second after the
// worker's start. (A carrier whose time is already spent is also driven by
// its clock alone, in TestAWorkerThatDoesNotReportReadyIsWaitedForTheTablesTime.)
// Each time the sentence gives the number, what it is counted from, and when
// the worker was started.
//
// The file is a hole: its length is set and nothing is written to it. A file
// system that keeps no holes would write the two gibibytes.
func TestTheWaitForReadinessIsCountedFromTheCarriersOwnStart(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	worker, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	root, p := nativeRuntimeFixture(t, worker)
	const name, size = "engine/weights", int64(2) << 30
	f, err := os.Create(filepath.Join(root, filepath.FromSlash(name)))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := f.Truncate(size); err != nil {
		t.Fatal(err)
	}
	h, read := sha256.New(), time.Now()
	if n, err := io.Copy(h, f); err != nil || n != size {
		t.Fatalf("read %d of the file's %d bytes: %v", n, size, err)
	}
	took := time.Since(read)
	p.Files[name] = runtimeFile{SHA256: hex.EncodeToString(h.Sum(nil)), Bytes: size}

	started := regexp.MustCompile(`the worker was started (\d+) ms into it`)
	for _, tc := range []struct {
		name  string
		ready time.Duration
	}{
		{"the check is inside the wait", max(readyFloor, 2*took).Round(100 * time.Millisecond)},
		{"the check leaves little of it or none", readyFloor},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p.Limits = &profileLimits{ReadyMS: ms(tc.ready.Milliseconds()), WarmProbeMS: ms(250)}
			log, waited, err := silentCarrier(t, root, p, "closes", notReady(tc.ready.Milliseconds()))
			var status *exec.ExitError
			if !errors.As(err, &status) || status.ExitCode() != 1 || !strings.Contains(log.String(), log.line) {
				t.Fatalf("the carrier did not end with status 1 saying its worker did not report ready in %v (%v) after %v:\n%s", tc.ready, err, waited, log.String())
			}
			// By the carrier's own records: when its check began and ended,
			// when it had started its worker, and when it gave up.
			checked := startupElapsed(t, log.String(), "runtime-verification-complete") - startupElapsed(t, log.String(), "runtime-verification-begin")
			begun := startupElapsed(t, log.String(), "worker-started-awaiting-warm-inference")
			gaveUp := startupElapsed(t, log.String(), "worker-readiness-not-reported")
			if checked < 200*time.Millisecond {
				t.Fatalf("the check of the runtime's files took %v: too little to tell a wait counted from the carrier's start from one counted from the worker's", checked)
			}
			// When ready_ms have passed since the carrier's start, or as soon
			// as the worker is started where they had passed already: never
			// ready_ms after the worker's start, which is the sum of the two.
			due := max(tc.ready, begun)
			if gaveUp < due || gaveUp > due+min(tc.ready, begun)/2 {
				t.Errorf("the profile gives readiness %v, the worker was started %v into the carrier's start, and the carrier gave up %v into it; it should be %v, and counted from the worker's start it would be %v", tc.ready, begun, gaveUp, due, tc.ready+begun)
			}
			said := started.FindStringSubmatch(log.String())
			if said == nil {
				t.Fatalf("the sentence does not say when the worker was started:\n%s", log.String())
			}
			if at, _ := strconv.ParseInt(said[1], 10, 64); time.Duration(at)*time.Millisecond > begun || begun-time.Duration(at)*time.Millisecond > 100*time.Millisecond {
				t.Errorf("the sentence says the worker was started %s ms into the carrier's start; the carrier's record of it says %v", said[1], begun)
			}
			t.Logf("the check of a file of %d bytes took %v of the carrier's start; the profile gives readiness %v; the worker was started at %v and the carrier gave up at %v, ending %v after it was started here", size, checked, tc.ready, begun, gaveUp, waited.Round(time.Millisecond))
		})
	}
}
