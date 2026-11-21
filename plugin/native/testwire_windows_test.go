package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

func handAudio(cmd *exec.Cmd, in, out *os.File) {
	handles := []syscall.Handle{syscall.Handle(in.Fd()), syscall.Handle(out.Fd())}
	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles}
	cmd.Env = append(cmd.Env, fmt.Sprintf("AII_AUDIO_IN_FD=%d", handles[0]), fmt.Sprintf("AII_AUDIO_OUT_FD=%d", handles[1]))
}

func passInherited(cmd *exec.Cmd) []*os.File {
	var files []*os.File
	var handles []syscall.Handle
	for _, name := range []string{"AII_AUDIO_IN_FD", "AII_AUDIO_OUT_FD"} {
		value, err := strconv.ParseUint(os.Getenv(name), 10, 64)
		if err != nil || value == 0 {
			continue
		}
		files = append(files, os.NewFile(uintptr(value), name))
		handles = append(handles, syscall.Handle(value))
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles}
	return files
}
