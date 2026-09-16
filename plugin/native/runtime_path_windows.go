package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

// Resolve the actual file through an opened handle, including a folder-mounted
// volume. filepath.EvalSymlinks fails on files below such a mount on our native
// Windows target even when Stat/open succeed. Do not fall back to an unresolved
// path on error. The caller still verifies the entire resolved runtime tree.
func runtimeExecutable(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", err
	}
	if !info.Mode().IsRegular() {
		return "", errors.New("runtime executable is not a regular file")
	}
	buf := make([]uint16, 32768)
	fn := syscall.NewLazyDLL("kernel32.dll").NewProc("GetFinalPathNameByHandleW")
	// FILE_NAME_NORMALIZED | VOLUME_NAME_DOS. Retain the extended-length prefix
	// returned by Windows; removing it would silently reduce valid path support.
	n, _, callErr := fn.Call(f.Fd(), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), 0)
	if n == 0 {
		// AppContainer cannot query the mount-point manager for a DOS name.
		// For OUR loaded image only, the OS module path is another authoritative
		// name. Never accept an arbitrary unresolved caller path on failure.
		if errors.Is(callErr, syscall.ERROR_ACCESS_DENIED) {
			loaded, e := os.Executable()
			if e == nil && filepath.IsAbs(loaded) {
				image, e := os.Stat(loaded)
				if e == nil && os.SameFile(info, image) {
					return loaded, nil
				}
			}
		}
		return "", fmt.Errorf("resolve runtime executable handle: %w", callErr)
	}
	if n >= uintptr(len(buf)) {
		return "", errors.New("resolved runtime executable path exceeds Windows bound")
	}
	real := syscall.UTF16ToString(buf[:n])
	if !filepath.IsAbs(real) {
		return "", errors.New("resolved runtime executable path is not absolute")
	}
	return real, nil
}
