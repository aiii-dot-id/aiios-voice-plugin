//go:build darwin || linux

package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

func inheritAudio(cmd *exec.Cmd) (func(), error) {
	files := []*os.File{}
	cleanup := func() {
		for _, f := range files {
			_ = f.Close()
		}
	}
	for i, name := range []string{"AII_AUDIO_IN_FD", "AII_AUDIO_OUT_FD"} {
		fd, err := strconv.Atoi(os.Getenv(name))
		if err != nil || fd < 3 {
			cleanup()
			return nil, fmt.Errorf("missing inherited %s", name)
		}
		dup, err := syscall.Dup(fd)
		if err != nil {
			cleanup()
			return nil, err
		}
		files = append(files, os.NewFile(uintptr(dup), name))
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%d", name, 3+i))
	}
	cmd.ExtraFiles = files
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cleanup, nil
}

func killWorker(cmd *exec.Cmd) error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
