package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestDarwinRuntimeExecutableWithDeniedAncestor(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if os.Getenv("UID_RUNTIME_SANDBOX_PROBE") == "1" {
		if _, err := filepath.EvalSymlinks(self); err == nil {
			t.Fatal("probe did not deny ancestor metadata")
		}
		actual, err := runtimeExecutable(self)
		if err != nil {
			t.Fatal(err)
		}
		a, err := os.Stat(actual)
		if err != nil {
			t.Fatal(err)
		}
		b, err := os.Stat(self)
		if err != nil {
			t.Fatal(err)
		}
		if !os.SameFile(a, b) {
			t.Fatal("resolved another file")
		}
		return
	}
	real, err := filepath.EvalSymlinks(self)
	if err != nil {
		t.Fatal(err)
	}
	profile := fmt.Sprintf(`(version 1)(allow default)(deny file-read-metadata (literal %q))`, filepath.Dir(real))
	cmd := exec.Command("/usr/bin/sandbox-exec", "-p", profile, real, "-test.run=^TestDarwinRuntimeExecutableWithDeniedAncestor$", "-test.count=1")
	cmd.Env = append(os.Environ(), "UID_RUNTIME_SANDBOX_PROBE=1")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("contained executable resolution: %v %s", err, out)
	}
}

func TestDarwinRuntimeExecutableResolvesOpenedFile(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "image")
	link := filepath.Join(dir, "link")
	if err := os.WriteFile(file, []byte("image"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(file, link); err != nil {
		t.Fatal(err)
	}
	real, err := runtimeExecutable(link)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := filepath.EvalSymlinks(file)
	if err != nil {
		t.Fatal(err)
	}
	if real != expected {
		t.Fatalf("got %q want %q", real, expected)
	}
	if _, err := runtimeExecutable(dir); err == nil {
		t.Fatal("directory accepted")
	}
	if _, err := runtimeExecutable(filepath.Join(dir, "missing")); err == nil {
		t.Fatal("missing accepted")
	}
}
