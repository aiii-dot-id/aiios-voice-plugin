package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type inventoryVerdict struct {
	seen  int
	bytes int64
	err   error
}

func inventoryFixture(t *testing.T, n int) (string, map[string]runtimeFile) {
	t.Helper()
	root := t.TempDir()
	files := map[string]runtimeFile{}
	for i := 0; i < n; i++ {
		name := fmt.Sprintf("%03d.bin", i)
		data := []byte(fmt.Sprintf("bound-content-%03d", i))
		if err := os.WriteFile(filepath.Join(root, name), data, 0644); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(data)
		files[name] = runtimeFile{SHA256: hex.EncodeToString(h[:]), Bytes: int64(len(data))}
	}
	return root, files
}

func TestRuntimeInventoryIsConcurrentAndBounded(t *testing.T) {
	root, files := inventoryFixture(t, 24)
	started := make(chan string, 24)
	release := make(chan struct{})
	var once sync.Once
	open := func() { once.Do(func() { close(release) }) }
	t.Cleanup(open)
	var active, peak atomic.Int32
	check := func(job runtimeJob, buffer []byte) error {
		n := active.Add(1)
		defer active.Add(-1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		started <- job.name
		<-release
		if len(buffer) != 128*1024 {
			return errors.New("unbounded verifier buffer")
		}
		return verifyRuntimeFile(job, buffer)
	}
	done := make(chan inventoryVerdict, 1)
	go func() {
		n, b, e := verifyInventory(root, "carrier", files, 8, check)
		done <- inventoryVerdict{n, b, e}
	}()
	for i := 0; i < 8; i++ {
		select {
		case <-started:
		case <-time.After(2 * time.Second):
			t.Fatal("verifier serialized independent file reads")
		}
	}
	select {
	case v := <-done:
		t.Fatalf("verification returned before reads retired: %+v", v)
	default:
	}
	if peak.Load() != 8 {
		t.Fatalf("worker bound: %d", peak.Load())
	}
	open()
	select {
	case v := <-done:
		if v.err != nil || v.seen != 24 || v.bytes != 24*17 || active.Load() != 0 || peak.Load() > 8 {
			t.Fatalf("complete census/retirement: %+v active=%d peak=%d", v, active.Load(), peak.Load())
		}
	case <-time.After(2 * time.Second):
		t.Fatal("verifier failed to retire")
	}
}

func TestRuntimeInventoryFailureWaitsForActiveReaders(t *testing.T) {
	root, files := inventoryFixture(t, 24)
	started := make(chan string, 24)
	failNow := make(chan struct{})
	release := make(chan struct{})
	failed := make(chan struct{})
	var openOnce, failOnce sync.Once
	open := func() { openOnce.Do(func() { close(release) }) }
	fail := func() { failOnce.Do(func() { close(failNow) }) }
	t.Cleanup(open)
	t.Cleanup(fail)
	var active atomic.Int32
	want := errors.New("injected runtime read failure")
	check := func(job runtimeJob, buffer []byte) error {
		active.Add(1)
		defer active.Add(-1)
		started <- job.name
		if job.name == "000.bin" {
			<-failNow
			close(failed)
			return want
		}
		<-release
		return verifyRuntimeFile(job, buffer)
	}
	done := make(chan inventoryVerdict, 1)
	go func() {
		n, b, e := verifyInventory(root, "carrier", files, 8, check)
		done <- inventoryVerdict{n, b, e}
	}()
	for i := 0; i < 8; i++ {
		select {
		case <-started:
		case <-time.After(2 * time.Second):
			t.Fatal("readers did not start")
		}
	}
	fail()
	<-failed
	select {
	case v := <-done:
		t.Fatalf("failure returned with readers still active: %+v", v)
	case <-time.After(50 * time.Millisecond):
	}
	open()
	select {
	case v := <-done:
		if !errors.Is(v.err, want) || active.Load() != 0 {
			t.Fatalf("lost cause or unretired reader: %+v active=%d", v, active.Load())
		}
	case <-time.After(2 * time.Second):
		t.Fatal("failed verifier deadlocked")
	}
}

func TestRuntimeInventoryAllFailuresRemainRefused(t *testing.T) {
	for _, workers := range []int{1, 4, 8} {
		for _, kind := range []string{"hash", "missing", "extra"} {
			t.Run(fmt.Sprintf("%d-%s", workers, kind), func(t *testing.T) {
				root, files := inventoryFixture(t, 64)
				switch kind {
				case "hash":
					if err := os.WriteFile(filepath.Join(root, "063.bin"), []byte("false-content-063"), 0644); err != nil {
						t.Fatal(err)
					}
				case "missing":
					if err := os.Remove(filepath.Join(root, "063.bin")); err != nil {
						t.Fatal(err)
					}
				case "extra":
					if err := os.WriteFile(filepath.Join(root, "064.bin"), []byte("extra"), 0644); err != nil {
						t.Fatal(err)
					}
				}
				_, _, err := verifyInventory(root, "carrier", files, workers, verifyRuntimeFile)
				if err == nil {
					t.Fatalf("%s accepted with %d workers", kind, workers)
				}
			})
		}
	}
	root, files := inventoryFixture(t, 1)
	for _, workers := range []int{0, -1, 9, 1000} {
		if _, _, err := verifyInventory(root, "carrier", files, workers, verifyRuntimeFile); err == nil {
			t.Fatal("unbounded worker setting admitted", workers)
		}
	}
	if _, _, err := verifyInventory(root, "carrier", files, 1, nil); err == nil {
		t.Fatal("missing checker accepted")
	}
}

func TestRuntimeInventoryHashMismatchIsNotJustSize(t *testing.T) {
	root, files := inventoryFixture(t, 1)
	data := []byte(strings.Repeat("x", int(files["000.bin"].Bytes)))
	if err := os.WriteFile(filepath.Join(root, "000.bin"), data, 0644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := verifyInventory(root, "carrier", files, 8, verifyRuntimeFile); err == nil || !strings.Contains(err.Error(), "hash mismatch") {
		t.Fatalf("same-size tamper bypassed hash: %v", err)
	}
}
