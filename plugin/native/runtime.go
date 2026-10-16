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
	Schema   string `json:"schema"`
	Platform string `json:"platform"`
	Arch     string `json:"arch"`
	Backend  string `json:"backend"`
	Native   string `json:"native,omitempty"`
	// Python, Bootstrap and Site are read only to be refused: a released
	// profile carries all three empty (pythonProfileRefused).
	Python      string                 `json:"python"`
	Bootstrap   string                 `json:"bootstrap"`
	Site        string                 `json:"site"`
	Files       map[string]runtimeFile `json:"files"`
	LibraryDirs []string               `json:"library_dirs,omitempty"`
	CoreMLCache string                 `json:"coreml_cache,omitempty"`
	// Limits is the build's table of time limits (limits.go); absent, or
	// a member of it absent, takes the default.
	Limits *profileLimits `json:"limits,omitempty"`
}

// A PYTHON-PROFILE PACKAGE IS REFUSED. A profile with no native member named
// an interpreter, a bootstrap script and a site directory (python, bootstrap,
// site) under the target's interpreter backend (mlx, cuda, windows-pocket),
// and the packaged carrier started it as `<python> -I -S -B <bootstrap>`.
// No released engine is one: every released desktop set is the native worker.
// The carrier still accepted one.
// It is refused before one file of its inventory is read. A stand-in worker
// in development is named on an unbound carrier's command line
// (workerCommand), never by a profile.
const pythonProfileRefused = "this runtime profile describes a Python engine (an interpreter started on a bootstrap script), and no released engine is one: the carrier starts only the native worker a profile names"

// pythonEngine says the profile names any part of an interpreter engine.
func (p *runtimeProfile) pythonEngine() bool {
	return p.Python != "" || p.Bootstrap != "" || p.Site != ""
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
	if p.Schema != "aiii.voice.native-runtime" || p.Platform != runtime.GOOS || p.Arch != runtime.GOARCH {
		return nil, errors.New("runtime profile does not match this target")
	}
	if p.Native == "" {
		if p.pythonEngine() {
			return nil, errors.New(pythonProfileRefused)
		}
		return nil, errors.New("runtime profile names no native worker")
	}
	// The signed, hash-bound profile chooses exactly one owner. Native
	// never searches PATH, invokes a shell, or falls back to Python.
	if p.Backend != "native" || p.pythonEngine() || !runtimePath(p.Native) || p.Native == carrierName || p.Native == "voice-runtime.json" {
		return nil, errors.New("ambiguous or unsafe native runtime entrypoint")
	}
	if item, bound := p.Files[p.Native]; !bound || !item.Executable || item.Bytes <= 0 {
		return nil, errors.New("native entrypoint is not a bound executable")
	}
	for name, item := range p.Files {
		if !runtimePath(name) || item.Bytes < 0 || len(item.SHA256) != 64 {
			return nil, fmt.Errorf("invalid runtime inventory: %s", name)
		}
	}
	if p.CoreMLCache != "" {
		if p.Platform != "darwin" || p.Native == "" || !runtimePath(p.CoreMLCache) {
			return nil, errors.New("Core ML cache must be a bound native Mac runtime directory")
		}
		bound := false
		for name := range p.Files {
			bound = bound || strings.HasPrefix(name, p.CoreMLCache+"/")
		}
		if !bound {
			return nil, errors.New("Core ML cache has no bound files")
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

// packagedCommand is packagedWorker without its limits, for what only
// needs the command.
func packagedCommand(executable, expected string) (*exec.Cmd, error) {
	cmd, _, err := packagedWorker(executable, expected)
	return cmd, err
}

// packagedWorker is the bound runtime's worker command and the time limits
// its signed profile states; the worker is handed the same limits in its
// environment.
func packagedWorker(executable, expected string) (*exec.Cmd, limits, error) {
	cmd, p, err := packagedRuntime(executable, expected)
	if err != nil {
		return nil, limits{}, err
	}
	l, err := limitsFrom(p.Limits)
	if err != nil {
		return nil, limits{}, err
	}
	cmd.Env = append(cmd.Env, l.environment())
	return cmd, l, nil
}

func packagedRuntime(executable, expected string) (*exec.Cmd, *runtimeProfile, error) {
	startupPhase("executable-identity-begin", 0, 0)
	real, err := runtimeExecutable(executable)
	if err != nil {
		return nil, nil, err
	}
	startupPhase("executable-identity-complete", 0, 0)
	root := filepath.Dir(real)
	p, err := verifyRuntime(root, filepath.Base(real), expected)
	if err != nil {
		return nil, nil, err
	}
	if os.Getenv("AII_MODELS_DIR") == "" {
		return nil, nil, errors.New("host-provided AII_MODELS_DIR is required")
	}
	// The one launch there is: the bound native worker, with no argument.
	// verifyRuntime has refused every profile that names none.
	cmd := exec.Command(filepath.Join(root, filepath.FromSlash(p.Native)))
	cmd.Dir = root
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "PYTHON") || strings.HasPrefix(key, "DYLD_") || strings.HasPrefix(key, "LD_") || key == "HF_HUB_OFFLINE" || key == "TRANSFORMERS_OFFLINE" || key == "AII_VOICE_CARRIER_LIVENESS_FD" || key == limitsEnv || strings.EqualFold(key, "ORT_DISABLE_TELEMETRY") || strings.EqualFold(key, "AII_VOICE_COREML_CACHE_DIR") {
			continue
		}
		cmd.Env = append(cmd.Env, entry)
	}
	// Disable before ORT initializes. Its API suppressor can be called only
	// after initialization and does not prevent the native telemetry owner.
	cmd.Env = append(cmd.Env, "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "ORT_DISABLE_TELEMETRY=1")
	if p.CoreMLCache != "" {
		cmd.Env = append(cmd.Env, "AII_VOICE_COREML_CACHE_DIR="+filepath.Join(root, filepath.FromSlash(p.CoreMLCache)))
	}
	if runtime.GOOS == "linux" && len(p.LibraryDirs) > 0 {
		dirs, err := runtimeLibraries(root, p)
		if err != nil {
			return nil, nil, err
		}
		cmd.Env = append(cmd.Env, "LD_LIBRARY_PATH="+strings.Join(dirs, string(os.PathListSeparator)))
	}
	return cmd, p, nil
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

// workerCommand is the worker to start and the time limits it and this
// carrier wait by.
func workerCommand(args []string) (*exec.Cmd, limits, error) {
	if len(args) > 0 {
		if packagedRuntimeSHA != "" {
			return nil, limits{}, errors.New("packaged carrier refuses development worker overrides")
		}
		cmd := exec.Command(args[0], args[1:]...)
		for _, entry := range os.Environ() {
			if key, _, _ := strings.Cut(entry, "="); key != limitsEnv {
				cmd.Env = append(cmd.Env, entry)
			}
		}
		cmd.Env = append(cmd.Env, defaultLimits.environment())
		return cmd, defaultLimits, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, limits{}, err
	}
	return packagedWorker(executable, packagedRuntimeSHA)
}
