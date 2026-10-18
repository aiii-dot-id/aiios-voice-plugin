package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestNoDurationIsTypedInThisCarrierButThoseListedWithAReason(t *testing.T) {
	listed := map[string][]string{

		"enrollment.go": {"10*time.Minute", "time.Minute"},
		"vocabulary.go": {"10*time.Minute", "time.Minute"},
		"waveform.go":   {"10*time.Minute", "time.Minute"},

		"main.go": {"10 * time.Millisecond"},

		"recording_store.go": {"2 * time.Minute"},

		"runtime_inventory.go": {"2*time.Second"},
	}
	typed := regexp.MustCompile(`(\d+\s*\*\s*)?\btime\.(Hour|Minute|Second|Millisecond|Microsecond|Nanosecond)\b`)
	files, err := filepath.Glob("*.go")
	if err != nil || len(files) < 10 {
		t.Fatalf("the carrier's source was not found here: %v %v", files, err)
	}
	for _, name := range files {
		if strings.HasSuffix(name, "_test.go") || name == "limits.go" {
			continue
		}
		source, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		var found []string
		for _, line := range strings.Split(string(source), "\n") {
			code, _, _ := strings.Cut(line, "//")
			found = append(found, typed.FindAllString(code, -1)...)
		}
		want := append([]string(nil), listed[name]...)
		sort.Strings(found)
		sort.Strings(want)
		if strings.Join(found, " | ") != strings.Join(want, " | ") {
			t.Errorf("%s: the durations typed in it are [%s]; those listed here with a reason are [%s]", name, strings.Join(found, " | "), strings.Join(want, " | "))
		}
	}
}

func TestThisCarrierWaitsForItsWorkersExitByTheTable(t *testing.T) {
	const (
		forced = "worker required forced cleanup: it did not exit in 800 ms after its input was closed, the time the limits table gives it (worker_exit_ms)"
		unseen = "worker reap unproven: its exit was not seen in 600 ms after it was killed, the time the limits table gives that (worker_reap_ms)"
	)
	ending := func() *carrier {
		c, _, _ := privateFixture(t)
		c.limits.Retire, c.limits.WorkerExit, c.limits.WorkerReap = 250*time.Millisecond, 800*time.Millisecond, 600*time.Millisecond
		if err := c.limits.valid(); err != nil {
			t.Fatal(err)
		}
		return c
	}
	ended := func(c *carrier, kill func() error) (time.Duration, error) {
		began := time.Now()
		err := c.endWorker(kill)
		return time.Since(began), err
	}

	c, kills := ending(), 0
	go func(exited chan<- error) {
		time.Sleep(100 * time.Millisecond)
		exited <- nil
	}(c.done)
	if after, err := ended(c, func() error { kills++; return nil }); err != nil || kills != 0 || after > 700*time.Millisecond {
		t.Fatalf("a worker that exited 100 ms into a wait of 800: killed %d time(s), after %v: %v", kills, after, err)
	}

	c, kills = ending(), 0
	after, err := ended(c, func() error {
		kills++
		c.done <- errors.New("signal: killed")
		return nil
	})
	if err == nil || kills != 1 || !strings.Contains(err.Error(), forced) || !strings.Contains(err.Error(), "signal: killed") || strings.Contains(err.Error(), "reap unproven") {
		t.Fatalf("a worker that did not exit: killed %d time(s): %v", kills, err)
	}
	if after < 800*time.Millisecond || after > 3*time.Second {
		t.Fatalf("the table gives a worker's exit 800 ms and the kill came after %v", after)
	}

	c, kills = ending(), 0
	after, err = ended(c, func() error { kills++; return nil })
	if err == nil || kills != 1 || !strings.Contains(err.Error(), forced) || !strings.Contains(err.Error(), unseen) {
		t.Fatalf("a worker whose exit was never seen: killed %d time(s): %v", kills, err)
	}
	if after < 1400*time.Millisecond || after > 4*time.Second {
		t.Fatalf("the table gives a worker's exit 800 ms and its reaping 600, and the carrier gave up after %v", after)
	}
}

func TestThisCarrierSaysWhyItKillsItsWorkerBeforeItDoes(t *testing.T) {
	const forced = "aii-voice-t3: worker required forced cleanup: it did not exit in 750 ms after its input was closed, the time the limits table gives it (worker_exit_ms)\n"
	log, err := os.Create(filepath.Join(t.TempDir(), "log"))
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	carriers := os.Stderr
	os.Stderr = log
	defer func() { os.Stderr = carriers }()

	c, _, _ := privateFixture(t)
	c.limits.Retire, c.limits.WorkerExit, c.limits.WorkerReap = 250*time.Millisecond, 750*time.Millisecond, 250*time.Millisecond
	said, kills := "", 0
	ended := c.endWorker(func() error {
		kills++
		written, err := os.ReadFile(log.Name())
		if err != nil {
			return err
		}
		said = string(written)
		c.done <- errors.New("signal: killed")
		return nil
	})
	os.Stderr = carriers
	if kills != 1 || ended == nil || !strings.Contains(ended.Error(), "signal: killed") {
		t.Fatalf("a worker that did not exit was killed %d time(s): %v", kills, ended)
	}
	if said != forced {
		t.Fatalf("when the kill came the carrier's log held %q; it holds, before the kill, %q", said, forced)
	}
}

type stalledStorage struct{}

func (stalledStorage) HostCallTo(ctx context.Context, _ string, _ any, _ any) (aiiosdk.Object, error) {
	<-ctx.Done()
	return nil, ctx.Err()
}

func TestThisCarriersOwnStorageOperationsAreGivenTheTablesTime(t *testing.T) {
	if got := defaultLimits.ownStorage(); got != 34*time.Second || got <= defaultLimits.HostWrite {
		t.Fatalf("by default one of the carrier's own storage operations is given %v; it is two reads of five seconds and two writes of twelve", got)
	}
	c, _, _ := privateFixture(t)
	c.limits.HostRead, c.limits.HostWrite = 250*time.Millisecond, 400*time.Millisecond
	const late = "(the host's storage did not answer in the 1300 ms the limits table gives this operation: twice host_read_ms and twice host_write_ms)"
	for name, call := range map[string]func(context.Context) (any, error){
		"vocabulary.list": func(ctx context.Context) (any, error) {
			return vocabularyCall(ctx, stalledStorage{}, "vocabulary.list", nil)
		},
		"recording.prune": func(ctx context.Context) (any, error) {
			return c.recordingStoreCall(ctx, stalledStorage{}, "recording.prune", nil)
		},
	} {
		began := time.Now()
		_, err := c.ownStorageCall(call)
		after := time.Since(began)
		if err == nil || !strings.HasSuffix(err.Error(), late) {
			t.Errorf("%s on a host that never answers: %v", name, err)
		}
		if after < 1300*time.Millisecond || after > 4*time.Second {
			t.Errorf("%s: the table gives it 1300 ms and it was given up after %v", name, after)
		}
	}

	refusal := errors.New("canonical recording_id required")
	if _, err := c.ownStorageCall(func(context.Context) (any, error) { return nil, refusal }); err != refusal {
		t.Fatalf("an operation's own refusal was changed: %v", err)
	}
	if result, err := c.ownStorageCall(func(context.Context) (any, error) { return "listed", nil }); err != nil || result != "listed" {
		t.Fatalf("an operation's own result was changed: %v %v", result, err)
	}
}
