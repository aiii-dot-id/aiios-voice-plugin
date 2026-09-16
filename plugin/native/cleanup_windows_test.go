package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"
)

func TestWindowsKillWithoutOwnedJobRefuses(t *testing.T) {
	if workerTreeJob != 0 {
		t.Fatal("fixture unexpectedly owns a process job")
	}
	if err := killWorker(nil); err == nil {
		t.Fatal("missing job was reported retired")
	}
}

// This helper has a hard lifetime even if its test owner disappears. The
// owner has exactly the carrier's process-lifetime job. Its caller observes
// both processes' actual exit; no self-reported retirement is accepted.
func TestWindowsCleanupFixture(t *testing.T) {
	role := os.Getenv("AII_TEST_CLEANUP_ROLE")
	if role == "" {
		return
	}
	if role == "leaf" {
		fmt.Println(os.Getpid())
		time.Sleep(60 * time.Second)
		return
	}
	if role != "parent" {
		t.Fatal("unknown fixture role")
	}
	if err := ownWorkerTree(); err != nil {
		t.Fatal(err)
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	child := exec.Command(exe, "-test.run=^TestWindowsCleanupFixture$")
	child.Env = append(os.Environ(), "AII_TEST_CLEANUP_ROLE=leaf")
	child.Stderr = os.Stderr
	out, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = child.Process.Kill(); _ = child.Wait() }()
	var ready int
	if _, err := fmt.Fscanln(bufio.NewReader(out), &ready); err != nil || ready != child.Process.Pid {
		t.Fatalf("descendant did not start: %d, %v", ready, err)
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]int{"parent": os.Getpid(), "child": ready}); err != nil {
		t.Fatal(err)
	}
	trigger := make(chan bool, 1)
	go func() {
		var line string
		_, err := fmt.Fscanln(os.Stdin, &line)
		trigger <- err == nil && line == "terminate"
	}()
	select {
	case yes := <-trigger:
		if !yes {
			t.Fatal("missing termination command")
		}
		if err := killWorker(child); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
		// Termination may complete asynchronously. Never turn a returned system
		// call into a fabricated successful test/session result.
		time.Sleep(5 * time.Second)
		t.Fatal("job termination did not retire its owner")
	case <-time.After(60 * time.Second):
		t.Fatal("fixture owner deadline")
	}
}

func TestWindowsKillWorkerWithoutPATHRetiresDescendant(t *testing.T) {
	// PATH is deliberately absent from the host's native AppContainer env.
	// Use a real running parent and grandchild, not an executable lookup mock.
	t.Setenv("PATH", "")
	t.Setenv("NoDefaultCurrentDirectoryInExePath", "1")
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^TestWindowsCleanupFixture$")
	cmd.Env = append(os.Environ(), "AII_TEST_CLEANUP_ROLE=parent")
	cmd.Stderr = os.Stderr
	in, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait() }()
	reaped := false
	t.Cleanup(func() {
		if !reaped {
			_ = cmd.Process.Kill()
			select {
			case <-waited:
			case <-time.After(5 * time.Second):
				t.Error("fixture parent cleanup not reaped")
			}
		}
	})
	type identity struct{ Parent, Child int }
	ready := make(chan identity, 1)
	go func() {
		var ids identity
		_ = json.NewDecoder(out).Decode(&ids)
		ready <- ids
	}()
	var ids identity
	select {
	case ids = <-ready:
	case <-time.After(20 * time.Second):
		t.Fatal("worker fixture readiness timed out")
	}
	if ids.Parent != cmd.Process.Pid || ids.Child <= 0 || ids.Child == ids.Parent {
		t.Fatalf("unproven fixture identities: %+v", ids)
	}
	child, err := syscall.OpenProcess(syscall.SYNCHRONIZE|syscall.PROCESS_TERMINATE, false, uint32(ids.Child))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = syscall.TerminateProcess(child, 1)
		status, err := syscall.WaitForSingleObject(child, 5000)
		_ = syscall.CloseHandle(child)
		if err != nil || status != syscall.WAIT_OBJECT_0 {
			t.Errorf("fixture descendant cleanup unproven: %d, %v", status, err)
		}
	})
	if status, err := syscall.WaitForSingleObject(child, 0); err != nil || status != syscall.WAIT_TIMEOUT {
		t.Fatalf("descendant was not alive before forced cleanup: %d, %v", status, err)
	}
	started := time.Now()
	if _, err := fmt.Fprintln(in, "terminate"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-waited:
		reaped = true
		if err == nil || cmd.ProcessState.ExitCode() != 73 {
			t.Fatalf("forced job retirement must be abnormal exit 73: %v (%v)", cmd.ProcessState, err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("forced cleanup did not reap worker")
	}
	if status, err := syscall.WaitForSingleObject(child, 5000); err != nil || status != syscall.WAIT_OBJECT_0 {
		t.Fatalf("forced cleanup left descendant alive: %d, %v", status, err)
	}
	t.Logf("PATH absent; parent %d reaped, descendant %d exited; elapsed=%s", ids.Parent, ids.Child, time.Since(started))
}
