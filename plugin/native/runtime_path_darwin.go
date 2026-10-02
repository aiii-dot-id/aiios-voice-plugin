package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"unsafe"
)

// Ask the kernel for the opened file's path. Walking every ancestor with
// EvalSymlinks requires metadata permissions outside the runtime sandbox.
// F_GETPATH preserves canonical resolution without widening that sandbox.
func runtimeExecutable(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "", err
	}
	if !st.Mode().IsRegular() {
		return "", errors.New("runtime executable is not a regular file")
	}
	var buf [1024]byte // Darwin MAXPATHLEN, required by F_GETPATH.
	_, _, errno := syscall.Syscall(syscall.SYS_FCNTL, f.Fd(), syscall.F_GETPATH, uintptr(unsafe.Pointer(&buf[0])))
	runtime.KeepAlive(f)
	if errno != 0 {
		return "", errno
	}
	n := bytes.IndexByte(buf[:], 0)
	if n <= 0 {
		return "", errors.New("runtime executable path missing or exceeds Darwin bound")
	}
	real := string(buf[:n])
	if !filepath.IsAbs(real) {
		return "", errors.New("runtime executable path is not absolute")
	}
	return real, nil
}
