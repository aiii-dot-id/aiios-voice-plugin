package main

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

func TestWindowsRuntimeExecutableResolvesActualFile(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "file")
	if err := os.WriteFile(file, []byte("bound"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{file, root, filepath.Join(root, "missing")} {
		real, err := runtimeExecutable(path)
		if path != file {
			if err == nil {
				t.Fatalf("non-file accepted: %q", path)
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		a, e1 := os.Stat(file)
		b, e2 := os.Stat(real)
		if e1 != nil || e2 != nil || !os.SameFile(a, b) {
			t.Fatalf("resolved a different file: %q (%v, %v)", real, e1, e2)
		}
	}
}

func TestPackagedCommandUnderWindowsVolumeMount(t *testing.T) {
	mount := os.Getenv("AII_TEST_VOLUME_MOUNT")
	if mount == "" {
		t.Skip("native mounted-volume gate must supply AII_TEST_VOLUME_MOUNT")
	}
	p, err := syscall.UTF16PtrFromString(mount)
	if err != nil {
		t.Fatal(err)
	}
	attrs, err := syscall.GetFileAttributes(p)
	if err != nil || attrs&syscall.FILE_ATTRIBUTE_REPARSE_POINT == 0 {
		t.Fatalf("gate fixture is not a mounted/reparse directory: %s (%v)", mount, err)
	}
	root, err := os.MkdirTemp(mount, "voice-runtime-path-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	root, digest := runtimeFixtureAt(t, root)
	executable := filepath.Join(root, "carrier")
	if err := os.WriteFile(executable, []byte("carrier"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	cmd, err := packagedCommand(executable, digest)
	if err != nil {
		t.Fatalf("readable runtime below mounted volume refused: %v", err)
	}
	a, e1 := os.Stat(cmd.Dir)
	b, e2 := os.Stat(root)
	if e1 != nil || e2 != nil || !os.SameFile(a, b) {
		t.Fatalf("resolved runtime is another directory: %s (%v, %v)", cmd.Dir, e1, e2)
	}
	if err := os.WriteFile(filepath.Join(root, "engine/boot.py"), []byte("evil"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := packagedCommand(executable, digest); err == nil {
		t.Fatal("mounted-volume support bypassed bound runtime verification")
	}
}
