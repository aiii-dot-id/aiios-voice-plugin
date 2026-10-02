package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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
			if loaded, e := loadedRuntimeExecutable(info); e == nil {
				return loaded, nil
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

// AppContainer's loaded-image fallback must retain the same extended-length
// semantics as GetFinalPathNameByHandleW. The native DLL loader fails before
// main with STATUS_NAME_TOO_LONG when a worker at 260 characters is launched
// through the otherwise identical unprefixed path. A longPathAware manifest
// does not cure that loader failure. Never substitute an unrelated executable.
func loadedRuntimeExecutable(expected os.FileInfo) (string, error) {
	loaded, err := os.Executable()
	if err != nil || !filepath.IsAbs(loaded) {
		return "", errors.New("loaded runtime executable path unavailable")
	}
	if !strings.HasPrefix(loaded, `\\?\`) {
		if strings.HasPrefix(loaded, `\\`) {
			loaded = `\\?\UNC\` + loaded[2:]
		} else {
			loaded = `\\?\` + loaded
		}
	}
	image, err := os.Stat(loaded)
	if err != nil || !os.SameFile(expected, image) {
		return "", errors.New("loaded runtime executable identity mismatch")
	}
	return loaded, nil
}
