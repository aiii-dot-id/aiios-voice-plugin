package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// This compiled test process is the native launch fixture, not a model or a
// synthetic speech pass. No interpreter or shell participates in its launch.
func init() {
	if os.Getenv("AII_NATIVE_LAUNCH_FIXTURE") == "1" {
		if len(os.Args) != 1 || !strings.HasPrefix(filepath.Base(os.Args[0]), "launch-fixture") {
			os.Exit(91)
		}
		if err := json.NewEncoder(os.Stdout).Encode(map[string]any{
			"native_fixture": true, "models": os.Getenv("AII_MODELS_DIR"),
			"pythonpath": os.Getenv("PYTHONPATH"), "path": os.Getenv("PATH"),
			"ort_telemetry_disabled": os.Getenv("ORT_DISABLE_TELEMETRY"),
		}); err != nil {
			os.Exit(92)
		}
		os.Exit(0)
	}
}

func nativeRuntimeFixture(t *testing.T, content []byte) (string, runtimeProfile) {
	t.Helper()
	root := t.TempDir()
	name := "launch-fixture"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	name = "engine/" + name
	if err := os.Mkdir(filepath.Join(root, "engine"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(name)), content, 0755); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256(content)
	p := runtimeProfile{Schema: "aiii.voice.native-runtime", Platform: runtime.GOOS,
		Arch: runtime.GOARCH, Backend: "native", Native: name,
		Files: map[string]runtimeFile{name: {SHA256: hex.EncodeToString(h[:]), Bytes: int64(len(content)), Executable: true}}}
	if err := os.WriteFile(filepath.Join(root, "carrier"), []byte("bound carrier fixture"), 0755); err != nil {
		t.Fatal(err)
	}
	return root, p
}

func writeRuntimeProfile(t *testing.T, root string, p runtimeProfile) string {
	t.Helper()
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "voice-runtime.json"), b, 0644); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func TestNativeRuntimeLaunchHasNoInterpreterOrPathFallback(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	root, p := nativeRuntimeFixture(t, b)
	digest := writeRuntimeProfile(t, root, p)
	models := t.TempDir()
	t.Setenv("AII_MODELS_DIR", models)
	t.Setenv("AII_NATIVE_LAUNCH_FIXTURE", "1")
	t.Setenv("PATH", "")
	t.Setenv("PYTHONPATH", "/developer-python-must-not-be-visible")
	t.Setenv("ORT_DISABLE_TELEMETRY", "0")
	cmd, err := packagedCommand(filepath.Join(root, "carrier"), digest)
	if err != nil {
		t.Fatal(err)
	}
	if len(cmd.Args) != 1 || !strings.HasSuffix(filepath.ToSlash(cmd.Path), p.Native) {
		t.Fatalf("native launch passed through an interpreter or shell: %v", cmd.Args)
	}
	output, err := cmd.Output()
	if err != nil {
		t.Fatalf("actual compiled native child did not run: %v", err)
	}
	var got struct {
		Native bool   `json:"native_fixture"`
		Models string `json:"models"`
		Python string `json:"pythonpath"`
		Path   string `json:"path"`
		ORT    string `json:"ort_telemetry_disabled"`
	}
	if err := json.Unmarshal(output, &got); err != nil {
		t.Fatal(err)
	}
	if !got.Native || got.Models != models || got.Python != "" || got.Path != "" || got.ORT != "1" {
		t.Fatalf("native launch contract differs: %+v", got)
	}
	// Re-signing the profile is not part of this corruption. The original
	// executable binding must refuse changed worker bytes before any launch.
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(p.Native)), []byte("broken executable"), 0755); err != nil {
		t.Fatal(err)
	}
	if _, err := packagedCommand(filepath.Join(root, "carrier"), digest); err == nil {
		t.Fatal("changed native owner accepted")
	}
}

func TestNativeRuntimeRefusesAmbiguousOrUnboundOwner(t *testing.T) {
	for _, mutation := range []string{"python", "bootstrap", "site", "backend", "missing", "mode", "empty", "outside", "carrier", "manifest"} {
		t.Run(mutation, func(t *testing.T) {
			root, p := nativeRuntimeFixture(t, []byte("native fixture"))
			switch mutation {
			case "python":
				p.Python = "python/python"
			case "bootstrap":
				p.Bootstrap = "engine/boot.py"
			case "site":
				p.Site = "python/site"
			case "backend":
				p.Backend = "mlx"
			case "missing":
				delete(p.Files, p.Native)
			case "mode":
				q := p.Files[p.Native]
				q.Executable = false
				p.Files[p.Native] = q
			case "empty":
				q := p.Files[p.Native]
				q.Bytes = 0
				p.Files[p.Native] = q
			case "outside":
				p.Native = "../outside"
			case "carrier":
				p.Native = "carrier"
			case "manifest":
				p.Native = "voice-runtime.json"
			default:
				t.Fatal(fmt.Errorf("unknown mutation %q", mutation))
			}
			digest := writeRuntimeProfile(t, root, p)
			if _, err := verifyRuntime(root, "carrier", digest); err == nil {
				t.Fatalf("%s native owner accepted", mutation)
			}
		})
	}
}
