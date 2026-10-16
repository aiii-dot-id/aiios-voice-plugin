//go:build darwin || linux

package main

import (
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestWorkerLivenessHelper(t *testing.T) {
	if os.Getenv("AII_TEST_LIVENESS_HELPER") != "1" {
		return
	}
	fd, err := strconv.Atoi(os.Getenv("AII_VOICE_CARRIER_LIVENESS_FD"))
	if err != nil || fd < 3 {
		os.Exit(71)
	}
	f := os.NewFile(uintptr(fd), "carrier-liveness")
	var one [1]byte
	n, err := f.Read(one[:])
	if n == 0 && err == io.EOF {
		os.Exit(74)
	}
	os.Exit(75)
}

func TestCarrierOwnedPipeRetiresWorkerBeforeReadiness(t *testing.T) {
	inRead, inWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer inRead.Close()
	defer inWrite.Close()
	outRead, outWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer outRead.Close()
	defer outWrite.Close()
	t.Setenv("AII_AUDIO_IN_FD", strconv.Itoa(int(inRead.Fd())))
	t.Setenv("AII_AUDIO_OUT_FD", strconv.Itoa(int(outWrite.Fd())))
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^TestWorkerLivenessHelper$")
	cmd.Env = append(os.Environ(), "AII_TEST_LIVENESS_HELPER=1")
	cleanup, err := inheritAudio(cmd)
	if err != nil {
		t.Fatal(err)
	}
	if len(cmd.ExtraFiles) != 3 || !strings.Contains(strings.Join(cmd.Env, "\n"), "AII_VOICE_CARRIER_LIVENESS_FD=5") {
		cleanup()
		t.Fatal("worker liveness descriptor not bound to the carrier")
	}
	if err := cmd.Start(); err != nil {
		cleanup()
		t.Fatal(err)
	}
	// The worker has not announced readiness and has no stdin reader. Closing
	// the carrier's custody end must still retire it promptly.
	cleanup()
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if x, ok := err.(*exec.ExitError); !ok || x.ExitCode() != 74 {
			t.Fatalf("worker did not retire on carrier EOF: %v", err)
		}
	case <-time.After(2 * time.Second):
		_ = cmd.Process.Kill()
		<-done
		t.Fatal("worker remained alive after carrier EOF")
	}
}
