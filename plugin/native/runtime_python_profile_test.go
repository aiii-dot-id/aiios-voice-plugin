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

// pythonProfileFixture is the package the carrier used to start: a profile
// with no native member that names an interpreter, a bootstrap script and a
// site directory under the target's interpreter backend, with every file it
// names bound and on disk. It used to verify, and the
// packaged carrier started it as `<python> -I -S -B <bootstrap>`.
func pythonProfileFixture(t *testing.T) (string, runtimeProfile) {
	t.Helper()
	root := t.TempDir()
	files := map[string]runtimeFile{}
	for name, content := range map[string]string{"python/bin/python": "interpreter", "engine/boot.py": "code"} {
		path := filepath.Join(root, filepath.FromSlash(name))
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
		files[name] = runtimeFile{hex.EncodeToString(h[:]), int64(len(content)), name == "python/bin/python"}
	}
	if err := os.WriteFile(filepath.Join(root, "carrier"), []byte("bound carrier fixture"), 0755); err != nil {
		t.Fatal(err)
	}
	backend := "mlx"
	if runtime.GOOS == "windows" {
		backend = "windows-pocket"
	} else if runtime.GOOS == "linux" {
		backend = "cuda"
	}
	return root, runtimeProfile{Schema: "aiii.voice.native-runtime", Platform: runtime.GOOS, Arch: runtime.GOARCH, Backend: backend,
		Python: "python/bin/python", Bootstrap: "engine/boot.py", Site: "python/site", Files: files}
}

// A PYTHON-PROFILE PACKAGE IS REFUSED AT START, IN ONE SENTENCE. Every road
// from the packaged carrier's start to a worker command goes through
// verifyRuntime; each of them says what the profile is and that no released
// engine is one, and none of them yields a command to start.
func TestAPythonProfilePackageIsRefusedAtStart(t *testing.T) {
	if strings.ContainsAny(pythonProfileRefused, "\n\r") || !strings.Contains(pythonProfileRefused, "Python engine") || !strings.Contains(pythonProfileRefused, "no released engine is one") {
		t.Fatalf("the refusal is one sentence that says what the profile is and that no released engine is one: %q", pythonProfileRefused)
	}
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	for _, tc := range []struct {
		name   string
		change func(root string, p *runtimeProfile)
	}{
		{"the whole profile", func(string, *runtimeProfile) {}},
		{"an interpreter alone", func(_ string, p *runtimeProfile) { p.Bootstrap, p.Site = "", "" }},
		{"a bootstrap script alone", func(_ string, p *runtimeProfile) { p.Python, p.Site = "", "" }},
		{"a site directory alone", func(_ string, p *runtimeProfile) { p.Python, p.Bootstrap = "", "" }},
		{"under the native backend's name", func(_ string, p *runtimeProfile) { p.Backend = "native" }},
		// Refused for what it is, before one file of its inventory is read: a
		// profile whose files are gone is still told it is a Python engine,
		// not that a file is missing.
		{"with its files gone", func(root string, _ *runtimeProfile) {
			for _, dir := range []string{"python", "engine"} {
				if err := os.RemoveAll(filepath.Join(root, dir)); err != nil {
					t.Fatal(err)
				}
			}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root, p := pythonProfileFixture(t)
			tc.change(root, &p)
			digest := writeRuntimeProfile(t, root, p)
			carrier := filepath.Join(root, "carrier")
			if got, err := verifyRuntime(root, "carrier", digest); got != nil || err == nil || err.Error() != pythonProfileRefused {
				t.Fatalf("a Python profile was not refused as one: %+v %v", got, err)
			}
			if cmd, err := packagedCommand(carrier, digest); cmd != nil || err == nil || err.Error() != pythonProfileRefused {
				t.Fatalf("a Python profile was given a command: %v %v", cmd, err)
			}
			if cmd, _, err := packagedWorker(carrier, digest); cmd != nil || err == nil || err.Error() != pythonProfileRefused {
				t.Fatalf("a Python profile was given a worker: %v %v", cmd, err)
			}
		})
	}
}

// A profile that names nothing to start is refused too, and is not called a
// Python engine: it names none.
func TestAProfileThatNamesNoWorkerIsRefused(t *testing.T) {
	root, p := pythonProfileFixture(t)
	p.Python, p.Bootstrap, p.Site = "", "", ""
	digest := writeRuntimeProfile(t, root, p)
	if _, err := verifyRuntime(root, "carrier", digest); err == nil || err.Error() != "runtime profile names no native worker" {
		t.Fatalf("a profile with nothing to start: %v", err)
	}
}

// THE NATIVE PROFILE IS STILL ACCEPTED AND STARTED, in both forms a profile
// takes: the three interpreter members present and empty, which is what the
// package scripts write and their audit requires, and the three left out.
func TestTheNativeProfileIsStillAcceptedAndStarted(t *testing.T) {
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	for _, form := range []string{"interpreter members empty", "interpreter members left out"} {
		t.Run(form, func(t *testing.T) {
			root, p := nativeRuntimeFixture(t, []byte("native worker"))
			digest := writeRuntimeProfile(t, root, p)
			manifest := filepath.Join(root, "voice-runtime.json")
			raw, err := os.ReadFile(manifest)
			if err != nil {
				t.Fatal(err)
			}
			var members map[string]json.RawMessage
			if err := json.Unmarshal(raw, &members); err != nil {
				t.Fatal(err)
			}
			if form == "interpreter members empty" {
				for _, name := range []string{"python", "bootstrap", "site"} {
					if string(members[name]) != `""` {
						t.Fatalf("the fixture does not state %s empty: %s", name, raw)
					}
				}
			} else {
				for _, name := range []string{"python", "bootstrap", "site"} {
					delete(members, name)
				}
				if raw, err = json.Marshal(members); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(manifest, raw, 0644); err != nil {
					t.Fatal(err)
				}
				h := sha256.Sum256(raw)
				digest = hex.EncodeToString(h[:])
			}
			got, err := verifyRuntime(root, "carrier", digest)
			if err != nil || got == nil || got.Native != p.Native || got.pythonEngine() {
				t.Fatalf("the native profile was not accepted: %+v %v", got, err)
			}
			cmd, l, err := packagedWorker(filepath.Join(root, "carrier"), digest)
			if err != nil {
				t.Fatalf("the native profile was given no worker: %v", err)
			}
			if len(cmd.Args) != 1 || l != defaultLimits {
				t.Fatalf("the native worker's command and limits: %v %+v", cmd.Args, l)
			}
			started, e1 := os.Stat(cmd.Path)
			named, e2 := os.Stat(filepath.Join(root, filepath.FromSlash(p.Native)))
			if e1 != nil || e2 != nil || !os.SameFile(started, named) {
				t.Fatalf("the command is not the worker the profile names: %s (%v, %v)", cmd.Path, e1, e2)
			}
		})
	}
}

// A DEVELOPMENT CARRIER STILL STARTS THE WORKER IT IS HANDED. The refusal is
// of a profile; a carrier with no compiled binding is told its worker on its
// command line, whatever that worker is, and the tests' stand-in workers are
// started this way. Nothing here is run: the command is only built.
func TestADevelopmentCarrierStillStartsTheWorkerItIsHanded(t *testing.T) {
	if packagedRuntimeSHA != "" {
		t.Fatal("the test binary carries a runtime binding; it is built without one")
	}
	args := []string{filepath.Join(t.TempDir(), "python3"), "-m", "tests.stand_in_worker", "--fixture"}
	cmd, l, err := workerCommand(args)
	if err != nil || !reflect.DeepEqual(cmd.Args, args) || cmd.Path != args[0] || l != defaultLimits {
		t.Fatalf("a development carrier's named worker: %v %+v %v", cmd, l, err)
	}
	// With no argument an unbound carrier has nothing to start, and says so.
	if cmd, _, err := workerCommand(nil); cmd != nil || err == nil || !strings.Contains(err.Error(), "developer carrier needs explicit worker arguments") {
		t.Fatalf("an unbound carrier with no worker named: %v %v", cmd, err)
	}
}
