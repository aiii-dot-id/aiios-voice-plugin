package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Set only by the payload builder. The host owns immutable extraction and
// signature verification; this binding prevents mixing code/runtime releases.
var packagedRuntimeSHA string

type runtimeFile struct {
	SHA256     string `json:"sha256"`
	Bytes      int64  `json:"bytes"`
	Executable bool   `json:"executable"`
}
type runtimeProfile struct {
	Schema      string                 `json:"schema"`
	Platform    string                 `json:"platform"`
	Arch        string                 `json:"arch"`
	Backend     string                 `json:"backend"`
	Native      string                 `json:"native,omitempty"`
	Python      string                 `json:"python"`
	Bootstrap   string                 `json:"bootstrap"`
	Site        string                 `json:"site"`
	Files       map[string]runtimeFile `json:"files"`
	LibraryDirs []string               `json:"library_dirs,omitempty"`
}

func runtimePath(name string) bool {
	return name != "." && fs.ValidPath(name) && !strings.ContainsAny(name, "\\:") && filepath.IsLocal(filepath.FromSlash(name))
}

func verifyRuntime(root, carrierName, expected string) (*runtimeProfile, error) {
	startupPhase("runtime-verification-begin", 0, 0)
	if len(expected) != 64 {
		return nil, errors.New("no compiled runtime binding; developer carrier needs explicit worker arguments")
	}
	manifest := filepath.Join(root, "voice-runtime.json")
	st, err := os.Lstat(manifest)
	if err != nil {
		return nil, err
	}
	if !st.Mode().IsRegular() || st.Size() > 16<<20 {
		return nil, errors.New("invalid runtime manifest file")
	}
	data, err := os.ReadFile(manifest)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(data)
	if hex.EncodeToString(hash[:]) != expected {
		return nil, errors.New("runtime manifest binding mismatch")
	}
	var p runtimeProfile
	if err := json.Unmarshal(data, &p); err != nil {
		return nil, err
	}
	backend := "mlx"
	if runtime.GOOS == "windows" {
		backend = "windows-pocket"
	} else if runtime.GOOS == "linux" {
		backend = "cuda"
	}
	if p.Schema != "aiii.voice.native-runtime" || p.Platform != runtime.GOOS || p.Arch != runtime.GOARCH || (p.Backend != backend && !(p.Backend == "native" && p.Native != "")) {
		return nil, errors.New("runtime profile does not match this target")
	}
	if p.Native != "" {
		// The signed, hash-bound profile chooses exactly one owner. Native
		// never searches PATH, invokes a shell, or falls back to Python.
		if p.Backend != "native" || p.Python != "" || p.Bootstrap != "" || p.Site != "" || !runtimePath(p.Native) || p.Native == carrierName || p.Native == "voice-runtime.json" {
			return nil, errors.New("ambiguous or unsafe native runtime entrypoint")
		}
		item, bound := p.Files[p.Native]
		if !bound || !item.Executable || item.Bytes <= 0 {
			return nil, errors.New("native entrypoint is not a bound executable")
		}
	} else {
		for _, name := range []string{p.Python, p.Bootstrap, p.Site} {
			if !runtimePath(name) {
				return nil, errors.New("unsafe runtime entry path")
			}
		}
		if len(p.Files) == 0 || p.Files[p.Python].SHA256 == "" || (runtime.GOOS != "windows" && !p.Files[p.Python].Executable) || p.Files[p.Bootstrap].SHA256 == "" {
			return nil, errors.New("missing runtime entrypoints")
		}
	}
	for name, item := range p.Files {
		if !runtimePath(name) || item.Bytes < 0 || len(item.SHA256) != 64 {
			return nil, fmt.Errorf("invalid runtime inventory: %s", name)
		}
	}
	startupPhase("runtime-inventory-bound", 0, 0)
	// Fixed, bounded I/O concurrency. Admission still waits for the complete
	// inventory and every worker's retirement; there is no warm-cache bypass.
	workers := 4
	if runtime.GOOS == "windows" {
		workers = 8
	}
	seen, verifiedBytes, err := verifyInventory(root, carrierName, p.Files, workers, verifyRuntimeFile)
	if err != nil {
		return nil, err
	}
	startupPhase("runtime-verification-complete", seen, verifiedBytes)
	return &p, nil
}

func packagedCommand(executable, expected string) (*exec.Cmd, error) {
	startupPhase("executable-identity-begin", 0, 0)
	real, err := runtimeExecutable(executable)
	if err != nil {
		return nil, err
	}
	startupPhase("executable-identity-complete", 0, 0)
	root := filepath.Dir(real)
	p, err := verifyRuntime(root, filepath.Base(real), expected)
	if err != nil {
		return nil, err
	}
	if os.Getenv("AII_MODELS_DIR") == "" {
		return nil, errors.New("host-provided AII_MODELS_DIR is required")
	}
	var cmd *exec.Cmd
	if p.Native != "" {
		cmd = exec.Command(filepath.Join(root, filepath.FromSlash(p.Native)))
	} else {
		cmd = exec.Command(filepath.Join(root, filepath.FromSlash(p.Python)), "-I", "-S", "-B", filepath.Join(root, filepath.FromSlash(p.Bootstrap)))
	}
	cmd.Dir = root
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "PYTHON") || strings.HasPrefix(key, "DYLD_") || strings.HasPrefix(key, "LD_") || key == "HF_HUB_OFFLINE" || key == "TRANSFORMERS_OFFLINE" || strings.EqualFold(key, "ORT_DISABLE_TELEMETRY") {
			continue
		}
		cmd.Env = append(cmd.Env, entry)
	}
	// Disable before ORT initializes. Its API suppressor can be called only
	// after initialization and does not prevent the native telemetry owner.
	cmd.Env = append(cmd.Env, "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "ORT_DISABLE_TELEMETRY=1")
	if runtime.GOOS == "linux" && len(p.LibraryDirs) > 0 {
		dirs, err := runtimeLibraries(root, p)
		if err != nil {
			return nil, err
		}
		cmd.Env = append(cmd.Env, "LD_LIBRARY_PATH="+strings.Join(dirs, string(os.PathListSeparator)))
	}
	return cmd, nil
}

func runtimeLibraries(root string, p *runtimeProfile) ([]string, error) {
	if len(p.LibraryDirs) > 32 {
		return nil, errors.New("too many runtime library directories")
	}
	seen := map[string]bool{}
	var dirs []string
	for _, name := range p.LibraryDirs {
		if !runtimePath(name) || seen[name] {
			return nil, errors.New("unsafe or duplicate runtime library directory")
		}
		seen[name] = true
		bound := false
		for file := range p.Files {
			if strings.HasPrefix(file, name+"/") {
				bound = true
				break
			}
		}
		path := filepath.Join(root, filepath.FromSlash(name))
		st, err := os.Lstat(path)
		if err != nil {
			return nil, err
		}
		if !bound || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return nil, errors.New("runtime library directory is not bound")
		}
		dirs = append(dirs, path)
	}
	return dirs, nil
}

func workerCommand(args []string) (*exec.Cmd, error) {
	if len(args) > 0 {
		if packagedRuntimeSHA != "" {
			return nil, errors.New("packaged carrier refuses development worker overrides")
		}
		cmd := exec.Command(args[0], args[1:]...)
		cmd.Env = os.Environ()
		return cmd, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	return packagedCommand(executable, packagedRuntimeSHA)
}
