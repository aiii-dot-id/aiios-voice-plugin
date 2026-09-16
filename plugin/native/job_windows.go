package main

import (
	"fmt"
	"syscall"
	"unsafe"
)

// The carrier is placed in its own job BEFORE creating the model worker. Its
// descendants inherit membership, including a CUDA recognition grandchild.
// No breakaway flag is allowed. The non-inherited job handle is deliberately
// process-lifetime: closing it earlier would also kill this carrier. Windows
// closes it on normal exit or TerminateProcess and retires the remaining tree.
// This is process ownership, not a substitute for host sandbox containment.
var workerTreeJob syscall.Handle

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

func ownWorkerTree() error {
	kernel := syscall.NewLazyDLL("kernel32.dll")
	h, _, err := kernel.NewProc("CreateJobObjectW").Call(0, 0)
	if h == 0 {
		return fmt.Errorf("create worker ownership job: %w", err)
	}
	limits := jobExtendedLimits{Basic: jobBasicLimits{Flags: 0x2000}} // KILL_ON_JOB_CLOSE
	ok, _, err := kernel.NewProc("SetInformationJobObject").Call(
		h, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		_ = syscall.CloseHandle(syscall.Handle(h))
		return fmt.Errorf("set worker job lifetime: %w", err)
	}
	self, err := syscall.GetCurrentProcess()
	if err != nil {
		_ = syscall.CloseHandle(syscall.Handle(h))
		return err
	}
	ok, _, err = kernel.NewProc("AssignProcessToJobObject").Call(h, uintptr(self))
	if ok == 0 {
		_ = syscall.CloseHandle(syscall.Handle(h))
		return fmt.Errorf("bind carrier before spawning workers: %w", err)
	}
	workerTreeJob = syscall.Handle(h)
	return nil
}
