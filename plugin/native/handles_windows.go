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
	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles}
	if err := ownWorkerTree(); err != nil {
		cleanup()
		return nil, err
	}
	return cleanup, nil
}

func killWorker(_ *exec.Cmd) error {
	if workerTreeJob == 0 {
		return fmt.Errorf("worker ownership job is missing; retirement unproven")
	}
	// This is deliberately an ABNORMAL terminal outcome, never session_end.
	// The existing process-lifetime job includes this carrier and every child;
	// terminating it also retires us. It needs no external utility or PATH and
	// cannot escape or terminate the host's enclosing job. The host observes
	// our exit and owns final reap; we cannot claim to have observed our own exit.
	fmt.Fprintln(os.Stderr, "aii-voice-t3: forced worker-tree termination; carrier exits with its ownership job")
	ok, _, err := syscall.NewLazyDLL("kernel32.dll").NewProc("TerminateJobObject").Call(uintptr(workerTreeJob), 73)
	if ok == 0 {
		return fmt.Errorf("terminate worker ownership job: %w", err)
	}
	return fmt.Errorf("worker-tree termination requested; host must observe retirement")
}
