//go:build !windows

package main

import (
	"errors"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestAWorkerTheCarrierCouldNotAdoptIsReapedWithinItsTime(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() })
	adopt := errors.New("put the worker in its own job: refused")
	const reap = 300 * time.Millisecond
	began := time.Now()
	err := reapUnadopted(cmd, adopt, reap)
	if took := time.Since(began); took > reap+2*time.Second {
		t.Fatalf("the carrier waited %s on a worker nothing ends, past its reap time of %s", took, reap)
	}
	if !errors.Is(err, adopt) || err == nil || !strings.Contains(err.Error(), "worker reap unproven") || !strings.Contains(err.Error(), "worker_reap_ms") {
		t.Fatalf("the adoption's error and the unproven reap are not both said: %v", err)
	}
}

func TestAWorkerThatEndsAfterAFailedAdoptionIsReapedAtOnce(t *testing.T) {
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Process.Signal(syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	adopt := errors.New("resume the worker in its job: refused")
	began := time.Now()
	err := reapUnadopted(cmd, adopt, 30*time.Second)
	if took := time.Since(began); took > 5*time.Second {
		t.Fatalf("an ended worker was not reaped at once: %s", took)
	}
	var exit *exec.ExitError
	if !errors.Is(err, adopt) || !errors.As(err, &exit) || strings.Contains(err.Error(), "unproven") {
		t.Fatalf("want the adoption's error and the worker's exit, got %v", err)
	}
}
