package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"
)

func TestWindowsKillWithoutOwnedJobRefuses(t *testing.T) {
	if workerTreeJob != 0 || workerJob != 0 {
		t.Fatal("fixture unexpectedly owns a process job")
	}
	if err := killWorker(nil); err == nil {
		t.Fatal("missing job was reported retired")
	}
}

type cleanupIdentities struct{ Carrier, Worker, Leaf int }

func TestWindowsCleanupFixture(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	start := func(role string, suspended bool) (*exec.Cmd, int) {
		cmd := exec.Command(exe, "-test.run=^TestWindowsCleanupFixture$")
		cmd.Env = append(os.Environ(), "AII_TEST_CLEANUP_ROLE="+role)
		cmd.Stderr = os.Stderr
		if suspended {
			cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: createSuspended}
		}
		out, err := cmd.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		returned := false
		defer func() {
			if !returned {
				_ = cmd.Process.Kill()
				_ = cmd.Wait()
			}
		}()
		if suspended {
			if err := adoptWorker(cmd); err != nil {
				t.Fatal(err)
			}
		}
		var pid int
		if _, err := fmt.Fscanln(bufio.NewReader(out), &pid); err != nil || pid <= 0 {
			t.Fatalf("the %s did not start: %d, %v", role, pid, err)
		}
		returned = true
		return cmd, pid
	}
	switch os.Getenv("AII_TEST_CLEANUP_ROLE") {
	case "":
		return
	case "leaf":
		fmt.Println(os.Getpid())
		time.Sleep(60 * time.Second)
	case "worker":
		leaf, pid := start("leaf", false)
		defer func() { _ = leaf.Process.Kill(); _ = leaf.Wait() }()
		fmt.Println(pid)
		time.Sleep(60 * time.Second)
	case "carrier":
		if err := ownWorkerTree(); err != nil {
			t.Fatal(err)
		}
		worker, leaf := start("worker", true)
		waited := make(chan error, 1)
		go func() { waited <- worker.Wait() }()
		if err := json.NewEncoder(os.Stdout).Encode(cleanupIdentities{os.Getpid(), worker.Process.Pid, leaf}); err != nil {
			t.Fatal(err)
		}
		var line string
		if _, err := fmt.Fscanln(os.Stdin, &line); err != nil || line != "terminate" {
			t.Fatalf("missing termination command: %q, %v", line, err)
		}
		if err := killWorker(worker); err != nil {
			t.Fatal(err)
		}
		select {
		case <-waited:
			fmt.Printf("worker exit %d\n", worker.ProcessState.ExitCode())
		case <-time.After(5 * time.Second):
			t.Fatal("the worker's job ended and its exit was not seen")
		}
	default:
		t.Fatal("unknown fixture role")
	}
}

func startCleanupCarrier(t *testing.T) (*exec.Cmd, <-chan error, cleanupIdentities, io.Writer, *bufio.Reader) {
	t.Helper()
	t.Setenv("PATH", "")
	t.Setenv("NoDefaultCurrentDirectoryInExePath", "1")
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^TestWindowsCleanupFixture$")
	cmd.Env = append(os.Environ(), "AII_TEST_CLEANUP_ROLE=carrier")
	cmd.Stderr = os.Stderr
	in, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = in.Close() })
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	waited, reaped := make(chan error, 1), make(chan struct{})
	go func() { waited <- cmd.Wait(); close(reaped) }()
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		select {
		case <-reaped:
		case <-time.After(5 * time.Second):
			t.Error("fixture carrier cleanup not reaped")
		}
	})
	rest := bufio.NewReader(out)
	ready := make(chan cleanupIdentities, 1)
	go func() {
		var ids cleanupIdentities
		line, _ := rest.ReadBytes('\n')
		_ = json.Unmarshal(line, &ids)
		ready <- ids
	}()
	var ids cleanupIdentities
	select {
	case ids = <-ready:
	case <-time.After(20 * time.Second):
		t.Fatal("cleanup fixture readiness timed out")
	}
	if ids.Carrier != cmd.Process.Pid || ids.Worker <= 0 || ids.Leaf <= 0 || ids.Worker == ids.Carrier || ids.Leaf == ids.Worker {
		t.Fatalf("unproven fixture identities: %+v", ids)
	}
	return cmd, waited, ids, in, rest
}

func watchProcess(t *testing.T, pid int, role string) syscall.Handle {
	t.Helper()
	h, err := syscall.OpenProcess(syscall.SYNCHRONIZE|syscall.PROCESS_TERMINATE|syscall.PROCESS_QUERY_INFORMATION, false, uint32(pid))
	if err != nil {
		t.Fatalf("open the fixture's %s: %v", role, err)
	}
	t.Cleanup(func() {
		_ = syscall.TerminateProcess(h, 1)
		status, err := syscall.WaitForSingleObject(h, 5000)
		_ = syscall.CloseHandle(h)
		if err != nil || status != syscall.WAIT_OBJECT_0 {
			t.Errorf("fixture %s cleanup unproven: %d, %v", role, status, err)
		}
	})
	if status, err := syscall.WaitForSingleObject(h, 0); err != nil || status != syscall.WAIT_TIMEOUT {
		t.Fatalf("the fixture's %s was not alive: %d, %v", role, status, err)
	}
	return h
}

func ended(t *testing.T, h syscall.Handle, role, after string) uint32 {
	t.Helper()
	if status, err := syscall.WaitForSingleObject(h, 5000); err != nil || status != syscall.WAIT_OBJECT_0 {
		t.Fatalf("%s left the %s alive: %d, %v", after, role, status, err)
	}
	var code uint32
	if err := syscall.GetExitCodeProcess(h, &code); err != nil {
		t.Fatal(err)
	}
	return code
}

func TestWindowsKillWorkerWithoutPATHRetiresItsTreeAndNotTheCarrier(t *testing.T) {
	_, waited, ids, in, rest := startCleanupCarrier(t)
	worker, leaf := watchProcess(t, ids.Worker, "worker"), watchProcess(t, ids.Leaf, "worker's child")
	started := time.Now()
	if _, err := fmt.Fprintln(in, "terminate"); err != nil {
		t.Fatal(err)
	}
	if code := ended(t, worker, "worker", "forced cleanup"); code != 73 {
		t.Fatalf("forced cleanup must end the worker abnormally with 73, not %d", code)
	}
	ended(t, leaf, "worker's child", "forced cleanup")
	line, _ := rest.ReadString('\n')
	if line != "worker exit 73\n" {
		t.Fatalf("the carrier did not see its worker's exit after the kill: %q", line)
	}
	select {
	case err := <-waited:
		if err != nil {
			t.Fatalf("the carrier did not outlive its worker's cleanup: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the carrier did not end after its worker's cleanup")
	}
	t.Logf("PATH absent; worker %d and its child %d ended, carrier %d ended by itself; elapsed=%s", ids.Worker, ids.Leaf, ids.Carrier, time.Since(started))
}

func TestWindowsCarrierDeathRetiresItsWorkersTree(t *testing.T) {
	cmd, waited, ids, _, _ := startCleanupCarrier(t)
	worker, leaf := watchProcess(t, ids.Worker, "worker"), watchProcess(t, ids.Leaf, "worker's child")
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-waited:
	case <-time.After(5 * time.Second):
		t.Fatal("the killed carrier was not reaped")
	}
	ended(t, worker, "worker", "the carrier's death")
	ended(t, leaf, "worker's child", "the carrier's death")
}
