package main

import (
	"errors"
	"fmt"
	"os/exec"
	"syscall"
	"unsafe"
)

var workerTreeJob syscall.Handle

var workerJob syscall.Handle

type jobBasicLimits struct {
	ProcessTime, JobTime         int64
	Flags                        uint32
	MinWorkingSet, MaxWorkingSet uintptr
	ActiveProcesses              uint32
	Affinity                     uintptr
	Priority, Scheduling         uint32
}

type jobExtendedLimits struct {
	Basic                            jobBasicLimits
	IO                               [6]uint64
	ProcessMemory, JobMemory         uintptr
	PeakProcessMemory, PeakJobMemory uintptr
}

const (
	createSuspended      = 0x00000004
	processSetQuota      = 0x0100
	processSuspendResume = 0x0800
)

var kernel32 = syscall.NewLazyDLL("kernel32.dll")

func killOnCloseJob(what string) (syscall.Handle, error) {
	h, _, err := kernel32.NewProc("CreateJobObjectW").Call(0, 0)
	if h == 0 {
		return 0, fmt.Errorf("create %s: %w", what, err)
	}
	limits := jobExtendedLimits{Basic: jobBasicLimits{Flags: 0x2000}}
	ok, _, err := kernel32.NewProc("SetInformationJobObject").Call(
		h, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		_ = syscall.CloseHandle(syscall.Handle(h))
		return 0, fmt.Errorf("set %s lifetime: %w", what, err)
	}
	return syscall.Handle(h), nil
}

func assignToJob(job, process syscall.Handle) error {
	if ok, _, err := kernel32.NewProc("AssignProcessToJobObject").Call(uintptr(job), uintptr(process)); ok == 0 {
		return err
	}
	return nil
}

func ownWorkerTree() error {
	h, err := killOnCloseJob("worker ownership job")
	if err != nil {
		return err
	}
	self, err := syscall.GetCurrentProcess()
	if err != nil {
		_ = syscall.CloseHandle(h)
		return err
	}
	if err = assignToJob(h, self); err != nil {
		_ = syscall.CloseHandle(h)
		return fmt.Errorf("bind carrier before spawning workers: %w", err)
	}
	workerTreeJob = h
	return nil
}

func adoptWorker(cmd *exec.Cmd) error {

	process, err := syscall.OpenProcess(processSetQuota|syscall.PROCESS_TERMINATE|processSuspendResume, false, uint32(cmd.Process.Pid))
	if err != nil {
		err = fmt.Errorf("open the worker to own its tree: %w", err)
		if kerr := cmd.Process.Kill(); kerr != nil {
			err = errors.Join(err, fmt.Errorf("end the worker it could not open: %w", kerr))
		}
		return err
	}
	defer syscall.CloseHandle(process)
	job, err := killOnCloseJob("worker job")
	if err == nil {
		if err = assignToJob(job, process); err != nil {
			_ = syscall.CloseHandle(job)
			err = fmt.Errorf("put the worker in its own job: %w", err)
		}
	}
	if err != nil {
		if terr := syscall.TerminateProcess(process, 73); terr != nil {
			err = errors.Join(err, fmt.Errorf("end the worker it could not put in its job: %w", terr))
		}
		return err
	}
	workerJob = job
	if status, _, _ := syscall.NewLazyDLL("ntdll.dll").NewProc("NtResumeProcess").Call(uintptr(process)); status != 0 {
		err := fmt.Errorf("resume the worker in its job: NTSTATUS %#x", status)
		if terr := syscall.TerminateProcess(process, 73); terr != nil {
			err = errors.Join(err, fmt.Errorf("end the worker it could not resume: %w", terr))
		}
		return err
	}
	return nil
}
