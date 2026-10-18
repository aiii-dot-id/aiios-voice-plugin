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

	n, _, callErr := fn.Call(f.Fd(), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)), 0)
	if n == 0 {

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
