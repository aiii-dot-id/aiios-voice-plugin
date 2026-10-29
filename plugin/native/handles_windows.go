package main

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

func inheritAudio(cmd *exec.Cmd) (func(), error) {
	var handles []syscall.Handle
	cleanup := func() {
		for _, h := range handles {
			_ = syscall.CloseHandle(h)
		}
	}
	process, err := syscall.GetCurrentProcess()
	if err != nil {
		return nil, err
	}
	for _, name := range []string{"AII_AUDIO_IN_FD", "AII_AUDIO_OUT_FD"} {
		value, err := strconv.ParseUint(os.Getenv(name), 10, 64)
		if err != nil || value == 0 {
			cleanup()
			return nil, fmt.Errorf("missing inherited %s", name)
		}
		var dup syscall.Handle
		if err = syscall.DuplicateHandle(process, syscall.Handle(value), process, &dup, 0, true, syscall.DUPLICATE_SAME_ACCESS); err != nil {
			cleanup()
			return nil, err
		}
		handles = append(handles, dup)
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%d", name, dup))
	}

	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles, CreationFlags: createSuspended}
	if err := ownWorkerTree(); err != nil {
		cleanup()
		return nil, err
	}
	return cleanup, nil
}

func killWorker(_ *exec.Cmd) error {
	if workerJob == 0 {
		return fmt.Errorf("worker ownership job is missing; retirement unproven")
	}

	if ok, _, err := kernel32.NewProc("TerminateJobObject").Call(uintptr(workerJob), 73); ok == 0 {
		return fmt.Errorf("terminate worker job: %w", err)
	}
	return nil
}
