//go:build !windows

package main

import "path/filepath"

func runtimeExecutable(path string) (string, error) {
	return filepath.EvalSymlinks(path)
}
