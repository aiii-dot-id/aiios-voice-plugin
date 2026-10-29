package main

import (
	"errors"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

func startSuspended(t *testing.T) *exec.Cmd {
	t.Helper()
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^$")
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createSuspended}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _, _ = cmd.Process.Wait() })
	return cmd
}

func TestWindowsASuspendedWorkerTheCarrierCouldNotAdoptIsReapedWithinItsTime(t *testing.T) {
	cmd := startSuspended(t)
	adopt := errors.New("put the worker in its own job: refused")
	const reap = 300 * time.Millisecond
	began := time.Now()
	err := reapUnadopted(cmd, adopt, reap)
	if took := time.Since(began); took > reap+2*time.Second {
		t.Fatalf("the carrier waited %s on a suspended worker, past its reap time of %s", took, reap)
	}
	if !errors.Is(err, adopt) || !strings.Contains(err.Error(), "worker reap unproven") || !strings.Contains(err.Error(), "worker_reap_ms") {
		t.Fatalf("the adoption's error and the unproven reap are not both said: %v", err)
	}
}

func TestWindowsASuspendedWorkerEndedAfterAFailedAdoptionIsReapedAtOnce(t *testing.T) {
	cmd := startSuspended(t)
	process, err := syscall.OpenProcess(syscall.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err != nil {
		t.Fatal(err)
	}
	err = syscall.TerminateProcess(process, 73)
	_ = syscall.CloseHandle(process)
	if err != nil {
		t.Fatal(err)
	}
	adopt := errors.New("resume the worker in its job: refused")
	began := time.Now()
	err = reapUnadopted(cmd, adopt, 30*time.Second)
	if took := time.Since(began); took > 5*time.Second {
		t.Fatalf("an ended worker was not reaped at once: %s", took)
	}
	var exit *exec.ExitError
	if !errors.Is(err, adopt) || !errors.As(err, &exit) || exit.ExitCode() != 73 || strings.Contains(err.Error(), "unproven") {
		t.Fatalf("want the adoption's error and the worker's exit 73, got %v", err)
	}
}

func TestWindowsAnAdoptedWorkerIsResumed(t *testing.T) {
	if workerTreeJob != 0 || workerJob != 0 {
		t.Fatal("the test process unexpectedly owns a process job")
	}
	cmd := startSuspended(t)
	t.Cleanup(func() {
		for _, h := range []*syscall.Handle{&workerJob, &workerTreeJob} {
			if *h != 0 {
				_ = syscall.CloseHandle(*h)
				*h = 0
			}
		}
	})
	if err := adoptWorker(cmd); err != nil {
		t.Fatal(err)
	}
	if workerJob == 0 {
		t.Fatal("the worker's job was not kept for killWorker")
	}
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait() }()
	select {
	case err := <-waited:
		if err != nil {
			t.Fatalf("the adopted worker did not run to its own end: %v", err)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("the adopted worker was never resumed")
	}
}
