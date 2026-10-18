//go:build !windows && !darwin

package main

import "path/filepath"

func runtimeExecutable(path string) (string, error) {
	return filepath.EvalSymlinks(path)
}
