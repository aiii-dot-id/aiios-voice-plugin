package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func runtimeFixture(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	return runtimeFixtureAt(t, root)
}

func runtimeFixtureAt(t *testing.T, root string) (string, string) {
	t.Helper()
	files := map[string]runtimeFile{}
	for name, content := range map[string]string{"python/bin/python": "interpreter", "engine/boot.py": "code"} {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		mode := os.FileMode(0644)
		if name == "python/bin/python" {
			mode = 0755
		}
		if err := os.WriteFile(path, []byte(content), mode); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256([]byte(content))
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		files[name] = runtimeFile{hex.EncodeToString(h[:]), int64(len(content)), info.Mode()&0111 != 0}
	}
	backend := "mlx"
	if runtime.GOOS == "windows" {
		backend = "windows-pocket"
	} else if runtime.GOOS == "linux" {
		backend = "cuda"
	}
	p := runtimeProfile{Schema: "aiii.voice.native-runtime", Platform: runtime.GOOS, Arch: runtime.GOARCH, Backend: backend, Python: "python/bin/python", Bootstrap: "engine/boot.py", Site: "python/site", Files: files}
	data, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "voice-runtime.json"), data, 0644); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256(data)
	return root, hex.EncodeToString(h[:])
}

func TestPackagedRuntimeRefusesTamper(t *testing.T) {
	for _, change := range []string{"hash", "missing", "extra", "link", "manifest", "mode"} {
		t.Run(change, func(t *testing.T) {
			root, digest := runtimeFixture(t)
			if _, err := verifyRuntime(root, "carrier", digest); err != nil {
				t.Fatal(err)
			}
			code := filepath.Join(root, "engine/boot.py")
			var err error
			switch change {
			case "hash":
				err = os.WriteFile(code, []byte("evil"), 0644)
			case "missing":
				err = os.Remove(code)
			case "extra":
				err = os.WriteFile(filepath.Join(root, "extra.py"), []byte("code"), 0644)
			case "link":
				err = os.Symlink(code, filepath.Join(root, "link"))
			case "manifest":
				digest = strings.Repeat("0", 64)
			case "mode":
				if runtime.GOOS == "windows" {
					// NT has no POSIX executable bit. Changing declared metadata
					// without changing the compiled binding must still be refused.
					path := filepath.Join(root, "voice-runtime.json")
					data, readErr := os.ReadFile(path)
					if readErr != nil {
						t.Fatal(readErr)
					}
					var p runtimeProfile
					if err := json.Unmarshal(data, &p); err != nil {
						t.Fatal(err)
					}
					item := p.Files["engine/boot.py"]
					item.Executable = !item.Executable
					p.Files["engine/boot.py"] = item
					data, err = json.Marshal(p)
					if err != nil {
						t.Fatal(err)
					}
					err = os.WriteFile(path, data, 0644)
				} else {
					err = os.Chmod(code, 0755)
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			if _, err := verifyRuntime(root, "carrier", digest); err == nil {
				t.Fatalf("%s accepted", change)
			}
		})
	}
}

func TestWindowsProducerExecutableMetadata(t *testing.T) {
	root, _ := runtimeFixture(t)
	manifest := filepath.Join(root, "voice-runtime.json")
	data, err := os.ReadFile(manifest)
	if err != nil {
		t.Fatal(err)
	}
	var p runtimeProfile
	if err := json.Unmarshal(data, &p); err != nil {
		t.Fatal(err)
	}
	// A real Python-produced Windows inventory marks *.exe executable.
	// The file is deliberately not chmod-executable under Unix.
	name := "python/protoc.exe"
	path := filepath.Join(root, filepath.FromSlash(name))
	if err := os.WriteFile(path, []byte("good"), 0644); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256([]byte("good"))
	p.Files[name] = runtimeFile{hex.EncodeToString(h[:]), 4, true}
	data, err = json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(manifest, data, 0644); err != nil {
		t.Fatal(err)
	}
	h = sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	_, err = verifyRuntime(root, "carrier", digest)
	if runtime.GOOS != "windows" {
		if err == nil {
			t.Fatal("Unix execute mode mismatch accepted")
		}
		return
	}
	if err != nil {
		t.Fatalf("unchanged Python-produced executable rejected on Windows: %v", err)
	}
	if err := os.WriteFile(path, []byte("evil"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := verifyRuntime(root, "carrier", digest); err == nil {
		t.Fatal("same-size content tamper accepted on Windows")
	}
}

func TestPackagedCommandHasNoDeveloperFallback(t *testing.T) {
	root, digest := runtimeFixture(t)
	executable := filepath.Join(root, "carrier")
	if err := os.WriteFile(executable, []byte("carrier"), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	t.Setenv("PYTHONPATH", "/developer")
	t.Setenv("DYLD_LIBRARY_PATH", "/developer")
	t.Setenv("ORT_DISABLE_TELEMETRY", "0")
	cmd, err := packagedCommand(executable, digest)
	if err != nil {
		t.Fatal(err)
	}
	if len(cmd.Args) != 5 || !reflect.DeepEqual(cmd.Args[1:4], []string{"-I", "-S", "-B"}) {
		t.Fatal(cmd.Args)
	}
	// Compare actual file identities, not one path spelling. Windows final-path
	// resolution legitimately returns an extended-length DOS/UNC name.
	for _, pair := range [][2]string{{cmd.Dir, root}, {cmd.Path, filepath.Join(root, "python/bin/python")}, {cmd.Args[4], filepath.Join(root, "engine/boot.py")}} {
		a, e1 := os.Stat(pair[0])
		b, e2 := os.Stat(pair[1])
		if e1 != nil || e2 != nil || !os.SameFile(a, b) {
			t.Fatalf("command escaped its runtime: %v (%v, %v)", pair, e1, e2)
		}
	}
	ortDisabled := 0
	for _, item := range cmd.Env {
		if strings.Contains(item, "/developer") {
			t.Fatal(item)
		}
		key, _, _ := strings.Cut(item, "=")
		if strings.EqualFold(key, "ORT_DISABLE_TELEMETRY") {
			ortDisabled++
			if item != "ORT_DISABLE_TELEMETRY=1" {
				t.Fatal("telemetry suppression is not fixed before initialization:", item)
			}
		}
	}
	if ortDisabled != 1 {
		t.Fatal("missing or contradictory telemetry setting:", cmd.Env)
	}
	if _, err := packagedCommand(executable, ""); err == nil {
		t.Fatal("unbound launch accepted")
	}
	old := packagedRuntimeSHA
	packagedRuntimeSHA = digest
	t.Cleanup(func() { packagedRuntimeSHA = old })
	if _, err := workerCommand([]string{"/developer/python"}); err == nil {
		t.Fatal("packaged override accepted")
	}
}

func TestRuntimePathIsLocal(t *testing.T) {
	for _, name := range []string{"", ".", "../x", "/x", "a/../b", "a\\b", "C:x", "a//b"} {
		if runtimePath(name) {
			t.Fatalf("unsafe path accepted: %q", name)
		}
	}
}

func TestRuntimeLibrariesAreBoundDirectories(t *testing.T) {
	root, _ := runtimeFixture(t)
	p := &runtimeProfile{Files: map[string]runtimeFile{"engine/boot.py": {}}, LibraryDirs: []string{"engine"}}
	dirs, err := runtimeLibraries(root, p)
	if err != nil || !reflect.DeepEqual(dirs, []string{filepath.Join(root, "engine")}) {
		t.Fatalf("valid bound library: %v %v", dirs, err)
	}
	for _, names := range [][]string{{"/usr/lib"}, {"../outside"}, {"engine", "engine"}, {"python/bin"}, {"engine/boot.py"}} {
		p.LibraryDirs = names
		if _, err := runtimeLibraries(root, p); err == nil {
			t.Fatalf("unbound/unsafe library accepted: %v", names)
		}
	}
}
