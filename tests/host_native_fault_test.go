//go:build darwin || linux

// External installed-runtime proof. Copy into the pinned host's pluginhost
// package only for this opt-in run. No production host code is changed.
package pluginhost

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-os/internal/audio"
	"github.com/aiii-dot-id/aii-os/internal/broker"
	"github.com/aiii-dot-id/aii-os/internal/packagefmt"
	"github.com/aiii-dot-id/aii-os/internal/packagefmt/packagetest"
)

func faultCheck(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
func faultHash(raw []byte) string { h := sha256.Sum256(raw); return hex.EncodeToString(h[:]) }
func faultRead(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	faultCheck(t, err)
	return raw
}

// Same public package payload and production verifier/installer, under a fresh
// ephemeral test root. The changed engine/carrier are explicitly hash-bound.
// This is not a public signature, download proof, browser or physical audio.
func faultPackage(t *testing.T) (string, *Options) {
	t.Helper()
	parent := os.Getenv("AII_FAULT_PACKAGE")
	candidate := os.Getenv("AII_FAULT_CANDIDATE")
	if parent == "" || candidate == "" || os.Getenv("AII_FAULT_MODELS") == "" {
		t.Fatal("explicit package, candidate and models required")
	}
	raw := faultRead(t, parent)
	if faultHash(raw) != os.Getenv("AII_FAULT_PACKAGE_SHA256") {
		t.Fatal("parent package bytes differ")
	}
	gz, err := gzip.NewReader(bytes.NewReader(raw))
	faultCheck(t, err)
	defer gz.Close()
	tr := tar.NewReader(gz)
	spec := packagetest.PackageSpec{InstallFiles: map[string][]byte{}}
	for {
		h, e := tr.Next()
		if e == io.EOF {
			break
		}
		faultCheck(t, e)
		if h.Typeflag != tar.TypeReg {
			continue
		}
		root, name, ok := strings.Cut(h.Name, "/")
		if !ok {
			t.Fatal("package root missing")
		}
		spec.Root = root
		data, e := io.ReadAll(tr)
		faultCheck(t, e)
		if name == "manifest.json" {
			spec.Manifest = data
		}
		if path, ok := strings.CutPrefix(name, "install-root/"); ok {
			spec.InstallFiles[path] = data
		}
	}
	record := faultRead(t, filepath.Join(candidate, "freeze.json"))
	if faultHash(record) != os.Getenv("AII_FAULT_FREEZE_SHA256") {
		t.Fatal("candidate freeze differs")
	}
	var frozen struct {
		Passed  bool
		Carrier string `json:"carrier_sha256"`
		Runtime string `json:"runtime_manifest_sha256"`
	}
	faultCheck(t, json.Unmarshal(record, &frozen))
	if !frozen.Passed {
		t.Fatal("candidate integrity incomplete")
	}
	carrier := faultRead(t, filepath.Join(candidate, "runtime", "aii-voice-t3"))
	if faultHash(carrier) != frozen.Carrier {
		t.Fatal("carrier differs")
	}
	profileRaw := faultRead(t, filepath.Join(candidate, "runtime", "voice-runtime.json"))
	if faultHash(profileRaw) != frozen.Runtime {
		t.Fatal("runtime manifest differs")
	}
	var profile struct {
		Files map[string]struct {
			SHA        string `json:"sha256"`
			Bytes      int64  `json:"bytes"`
			Executable bool   `json:"executable"`
		}
	}
	faultCheck(t, json.Unmarshal(profileRaw, &profile))
	tree := t.TempDir()
	for name, row := range profile.Files {
		faultCheck(t, validateModelPath(name))
		data := faultRead(t, filepath.Join(candidate, "runtime", filepath.FromSlash(name)))
		if faultHash(data) != row.SHA || int64(len(data)) != row.Bytes {
			t.Fatal("runtime image differs", name)
		}
		target := filepath.Join(tree, filepath.FromSlash(name))
		faultCheck(t, os.MkdirAll(filepath.Dir(target), 0700))
		mode := os.FileMode(0644)
		if row.Executable {
			mode = 0755
		}
		faultCheck(t, os.WriteFile(target, data, mode))
	}
	faultCheck(t, os.WriteFile(filepath.Join(tree, "voice-runtime.json"), profileRaw, 0644))
	archive := filepath.Join(t.TempDir(), "runtime.tar.gz")
	f, err := os.Create(archive)
	faultCheck(t, err)
	inventory, digest, err := packagefmt.WriteTree(f, tree, "runtime", packagefmt.TreeLimits{})
	faultCheck(t, err)
	faultCheck(t, f.Close())
	inv, err := packagefmt.ParseInventory(inventory, packagefmt.TreeLimits{})
	faultCheck(t, err)
	info, err := os.Stat(archive)
	faultCheck(t, err)
	variant := hostVariantID()
	decls, err := ParseRuntimes(spec.InstallFiles[RuntimesFile])
	faultCheck(t, err)
	for i, d := range decls {
		if d.VariantID == variant {
			decls[i] = RuntimeDecl{VariantID: variant, URL: d.URL, SHA256: strings.TrimPrefix(digest, "sha256:"), Size: info.Size(), InstalledBytes: inv.InstalledBytes, Files: len(inv.Files), InventorySHA256: strings.TrimPrefix(packagefmt.InventoryDigest(inventory), "sha256:")}
		}
	}
	spec.InstallFiles[RuntimesFile], err = json.Marshal(map[string]any{"runtimes": decls})
	faultCheck(t, err)
	spec.InstallFiles["variants/"+variant+"/plugin"] = carrier
	var manifest map[string]any
	faultCheck(t, json.Unmarshal(spec.Manifest, &manifest))
	for _, raw := range manifest["variants"].([]any) {
		v := raw.(map[string]any)
		if v["variant_id"] == variant {
			v["artifact_hash"] = "sha256:" + frozen.Carrier
		}
	}
	manifest["package_hash"] = packagetest.ReferencePackageHash(spec.InstallFiles)
	spec.Manifest, err = json.Marshal(manifest)
	faultCheck(t, err)
	roots, role := platformRootsForTest(t)
	faultCheck(t, role.SignT3(&spec))
	pkg := writePkg(t, spec)
	models, err := ParseModels(spec.InstallFiles[ModelsFile])
	faultCheck(t, err)
	paths := map[string]string{}
	for _, m := range models {
		paths[m.URL] = filepath.Join(os.Getenv("AII_FAULT_MODELS"), filepath.FromSlash(m.Dest()))
	}
	for _, r := range decls {
		if r.VariantID == variant {
			paths[r.URL] = archive
		}
	}
	fetch := func(ctx context.Context, url string, offset int64, w io.Writer) (int64, error) {
		path, ok := paths[url]
		if !ok {
			return 0, fmt.Errorf("undeclared fixture asset")
		}
		if e := ctx.Err(); e != nil {
			return 0, e
		}
		f, e := os.Open(path)
		if e != nil {
			return 0, e
		}
		defer f.Close()
		if _, e = f.Seek(offset, io.SeekStart); e != nil {
			return 0, e
		}
		return io.Copy(w, f)
	}
	root := t.TempDir()
	host, err := broker.New(broker.Config{Store: newBrokerStore(t)})
	faultCheck(t, err)
	capture := &logSink{}
	t.Cleanup(func() { t.Log(capture.String()) })
	opts := supervisedOpts(Options{HostVersion: "0.1.10", Roots: roots, Broker: host, PluginModelsDir: filepath.Join(root, "models"), PluginRuntimeDir: filepath.Join(root, "runtime"), PluginDataDir: filepath.Join(root, "data"), ModelFetcher: fetch, RuntimeFetcher: fetch, RuntimeRoots: NewRuntimeRoots(), ReadyTimeout: map[string]time.Duration{"id.aiii.voice": 180 * time.Second}, Log: log.New(capture, "", 0)})
	t.Logf("candidate carrier=%s runtime=%s freeze=%s test_root=true", frozen.Carrier, frozen.Runtime, faultHash(record))
	return pkg, opts
}

func faultEvent(t *testing.T, v *VoiceSession, kind string) map[string]any {
	t.Helper()
	timer := time.NewTimer(60 * time.Second)
	defer timer.Stop()
	for {
		select {
		case e, ok := <-v.Observe():
			if !ok {
				t.Fatal("observer ended")
			}
			if e.Type == "failure" {
				t.Fatalf("unexpected engine failure %s", e.Raw)
			}
			if e.Type == kind {
				var value map[string]any
				faultCheck(t, json.Unmarshal(e.Raw, &value))
				return value
			}
		case <-timer.C:
			t.Fatalf("missing %s: fault=%v reason=%s", kind, v.Faulted(), v.FaultReason())
		}
	}
}
func faultOpen(t *testing.T, ap *ActivePlugin, id string) (*audio.Binding, *audio.CaptureSink) {
	t.Helper()
	plane := audio.NewPlane()
	sink := audio.NewCaptureSink(audio.Format{Rate: 48000, Channels: 1})
	faultCheck(t, plane.Register(&audio.Endpoint{ID: "speaker", Sink: sink}))
	binding, err := plane.BindOutput(id, "speaker", false)
	faultCheck(t, err)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	faultCheck(t, ap.Voice.Load().OpenWithAudio(ctx, id, binding, nil))
	faultEvent(t, ap.Voice.Load(), "session_ready")
	return binding, sink
}
func faultSpeak(t *testing.T, ap *ActivePlugin, id string, sink *audio.CaptureSink) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	faultCheck(t, ap.Voice.Load().SynthesizeFor(ctx, id, "reply", "A fresh reply follows verified recovery."))
	start := faultEvent(t, ap.Voice.Load(), "synthesis_start")
	stream := uint32(start["output_stream"].(float64))
	for sink.StreamEnd(stream) < 0 {
		select {
		case <-ctx.Done():
			t.Fatal(ctx.Err())
		case <-time.After(10 * time.Millisecond):
		}
	}
	count := sink.StreamEnd(stream)
	if count <= 0 || int64(len(sink.StreamPCM(stream))) != count*2 {
		t.Fatal("missing or truncated reply")
	}
	faultCheck(t, ap.Voice.Load().PlaybackReportFor(ctx, id, PlaybackReport{Stream: stream, Rendered: count, Rate: 48000, Channels: 1, Terminal: true, Outcome: "drained"}))
	faultCheck(t, ap.Voice.Load().CloseFor(ctx, id, "drain", "fault qualification"))
	faultEvent(t, ap.Voice.Load(), "session_end")
	t.Logf("reply %s samples48000=%d", id, count)
}
func faultWorker(t *testing.T, ap *ActivePlugin) int {
	t.Helper()
	raw, err := exec.Command("pgrep", "-P", strconv.Itoa(ap.sup.Pid())).Output()
	faultCheck(t, err)
	found := 0
	for _, value := range strings.Fields(string(raw)) {
		pid, e := strconv.Atoi(value)
		faultCheck(t, e)
		cmd, e := exec.Command("ps", "-p", value, "-o", "comm=").Output()
		faultCheck(t, e)
		if strings.Contains(string(cmd), "aii_voice_worker") {
			if found != 0 {
				t.Fatal("multiple workers")
			}
			found = pid
		}
	}
	if found == 0 {
		t.Fatal("owned worker missing")
	}
	return found
}
func TestVoiceInstalledFaultRecovery(t *testing.T) {
	if os.Getenv("AII_FAULT_PACKAGE") == "" {
		t.Fatal("opt-in fixture inputs required")
	}
	pkg, opts := faultPackage(t)
	activate := func() *ActivePlugin {
		ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
		defer cancel()
		ap, e := Activate(ctx, pkg, newRegistry(t), opts)
		faultCheck(t, e)
		t.Cleanup(func() {
			c, stop := context.WithTimeout(context.Background(), 15*time.Second)
			defer stop()
			if e := ap.Deactivate(c); e != nil {
				t.Error(e)
			}
		})
		if !ap.Contained || !ap.sup.Containment().Isolated() || ap.Readiness == nil || !ap.Readiness.Real() || ap.Readiness.ModelsLoaded != 5 {
			t.Fatal("real contained five-model activation missing")
		}
		return ap
	}
	ap := activate()
	_, sink := faultOpen(t, ap, "baseline")
	faultSpeak(t, ap, "baseline", sink)
	for _, mode := range []string{"kill", "stall"} {
		binding, old := faultOpen(t, ap, mode)
		// The application registers this observer when it owns a live session.
		// Reap, not an admitted abort or transport fault, releases its custody.
		released := ap.PinReleased()
		worker := faultWorker(t, ap)
		// The session the fault is induced on; the host may publish a
		// successor binding, which must not answer for this one.
		voice := ap.Voice.Load()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		err := voice.SynthesizeFor(ctx, mode, "interrupted-reply", strings.Repeat("This reply must retire with its failed worker. ", 20))
		cancel()
		faultCheck(t, err)
		faultEvent(t, voice, "synthesis_start")
		signal := syscall.SIGKILL
		if mode == "stall" {
			signal = syscall.SIGSTOP
		}
		faultCheck(t, syscall.Kill(worker, signal))
		started := time.Now()
		if mode == "stall" {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			_, err := voice.Status(ctx)
			cancel()
			if err == nil {
				t.Fatal("stalled worker answered status")
			}
		}
		for !voice.Faulted() {
			if time.Since(started) > 12*time.Second {
				t.Fatal("worker loss not surfaced")
			}
			time.Sleep(10 * time.Millisecond)
		}
		t.Logf("%s observed after %s: %s", mode, time.Since(started), voice.FaultReason())
		ctx, cancel = context.WithTimeout(context.Background(), 15*time.Second)
		err = ap.Deactivate(ctx)
		cancel()
		faultCheck(t, err)
		select {
		case <-released:
		case <-time.After(time.Second):
			t.Fatal("verified retirement did not release the session pin")
		}
		select {
		case <-binding.Released():
		case <-time.After(time.Second):
			t.Fatal("dead session kept endpoints")
		}
		if syscall.Kill(worker, 0) == nil {
			t.Fatal("worker survived retirement")
		}
		if opts.RuntimeRoots.Refs(ap.RuntimeRoot) != 0 {
			t.Fatal("retired runtime still pinned")
		}
		before := len(old.PCM())
		ap = activate()
		_, fresh := faultOpen(t, ap, "recovery-"+mode)
		faultSpeak(t, ap, "recovery-"+mode, fresh)
		if len(old.PCM()) != before {
			t.Fatal("successor output reached dead sink")
		}
	}
}

// Recorded capture through the installed carrier, VAD, multi-talker recognizer
// and UID observation path. This checks custody/joins, not human transcription
// accuracy or identity correctness; neither is inferred from an event count.
func TestVoiceInstalledCaptureContinuity(t *testing.T) {
	runVoiceInstalledCapture(t, false)
}

// This opt-in gate supplies hash-bound COPIES of enrollment and registry data
// to a fresh test identity. It checks actual returned identities, not merely
// an observation count, without changing a live identity or its profiles.
func TestVoiceInstalledSpeakerIdentity(t *testing.T) {
	runVoiceInstalledCapture(t, true)
}

func runVoiceInstalledCapture(t *testing.T, identify bool) {
	input := faultRead(t, os.Getenv("AII_FAULT_RECORDING"))
	if faultHash(input) != os.Getenv("AII_FAULT_RECORDING_SHA256") {
		t.Fatal("recording bytes differ")
	}
	f := audio.Format{Rate: 16000, Channels: 1}
	file, err := audio.NewFileSource(bytes.NewReader(input), f, 512)
	faultCheck(t, err)
	if file.Format() != f {
		t.Fatal("16 kHz mono recording required")
	}
	var pcm []byte
	for {
		frame, err := file.Read(context.Background())
		if err == io.EOF {
			break
		}
		faultCheck(t, err)
		pcm = append(pcm, frame.PCM...)
		if len(pcm) > 60*16000*2 {
			t.Fatal("recording exceeds one-minute fixture bound")
		}
	}
	if len(pcm) < 32000*2 {
		t.Fatal("recording too short")
	}
	// Two separate utterance periods, then a complete source tail.
	period := append(append([]byte{}, pcm...), make([]byte, 4*16000*2)...)
	sourcePCM := append(append([]byte{}, period...), period...)
	source, err := audio.NewFileSource(bytes.NewReader(sourcePCM), f, 512)
	faultCheck(t, err)
	pkg, opts := faultPackage(t)
	if identify {
		if os.Getenv("AII_FAULT_EXPECTED_SPEAKER") == "" {
			t.Fatal("explicit expected speaker required")
		}
		uid := filepath.Join(opts.PluginDataDir, sanitizeToken("id.aiii.voice"), "uid")
		faultCheck(t, os.MkdirAll(uid, 0700))
		for _, item := range []struct{ name, key string }{{"enrollment.json", "AII_FAULT_ENROLLMENT"}, {"speakers.json", "AII_FAULT_REGISTRY"}} {
			raw := faultRead(t, os.Getenv(item.key))
			if faultHash(raw) != os.Getenv(item.key+"_SHA256") {
				t.Fatal("speaker fixture binding differs")
			}
			faultCheck(t, os.WriteFile(filepath.Join(uid, item.name), raw, 0600))
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	ap, err := Activate(ctx, pkg, newRegistry(t), opts)
	faultCheck(t, err)
	t.Cleanup(func() {
		c, done := context.WithTimeout(context.Background(), 15*time.Second)
		defer done()
		faultCheck(t, ap.Deactivate(c))
	})
	if !ap.Contained || !ap.sup.Containment().Isolated() || ap.Readiness == nil || ap.Readiness.ModelsLoaded != 5 {
		t.Fatal("real contained activation missing")
	}
	plane := audio.NewPlane()
	sink := audio.NewCaptureSink(audio.Format{Rate: 48000, Channels: 1})
	faultCheck(t, plane.Register(&audio.Endpoint{ID: "recorded-mic", Source: source}))
	faultCheck(t, plane.Register(&audio.Endpoint{ID: "speaker", Sink: sink}))
	binding, err := plane.Bind("recorded-continuity", "recorded-mic", "speaker", false)
	faultCheck(t, err)
	faultCheck(t, ap.Voice.Load().OpenWithAudio(ctx, "recorded-continuity", binding, map[string]any{"mode": "meeting"}))
	released := ap.PinReleased()
	finals := map[int64]bool{}
	observations := map[int64]bool{}
	returningUUID := ""
	starts, completions := 0, 0
	closed := false
	for !closed {
		select {
		case event, ok := <-ap.Voice.Load().Observe():
			if !ok {
				t.Fatal("observation lane ended")
			}
			var body map[string]any
			faultCheck(t, json.Unmarshal(event.Raw, &body))
			switch event.Type {
			case "failure":
				t.Fatal("engine failure during recorded capture")
			case "speech_start":
				starts++
			case "synthesis_start":
				t.Fatal("meeting unexpectedly synthesized")
			case "transcript_final":
				seq := int64(body["sequence"].(float64))
				if finals[seq] {
					t.Fatal("duplicate final")
				}
				finals[seq] = true
				lo, hi := int64(body["start_sample"].(float64)), int64(body["end_sample"].(float64))
				if lo < 0 || lo >= hi || hi > int64(len(sourcePCM)/2) || body["text"] == "" {
					t.Fatal("invalid final extent/content")
				}
			case "speaker_observation":
				ref := int64(body["refers_to"].(float64))
				if observations[ref] {
					t.Fatal("duplicate speaker observation")
				}
				observations[ref] = true
				if identify && (body["decision"] != "known" || body["speaker_id"] != os.Getenv("AII_FAULT_EXPECTED_SPEAKER")) {
					t.Fatalf("speaker acceptance failed: decision=%v reason=%v", body["decision"], body["reason"])
				}
				if identify && os.Getenv("AII_FAULT_REQUIRE_UUID") == "1" {
					id, _ := body["speaker_uuid"].(string)
					label, _ := body["display_label"].(string)
					revision, _ := body["registry_revision"].(string)
					scope, _ := body["evidence_scope"].(map[string]any)
					decoded, err := hex.DecodeString(strings.ReplaceAll(id, "-", ""))
					if err != nil || len(decoded) != 16 || len(id) != 36 || id[8] != '-' || id[13] != '-' || id[18] != '-' || id[23] != '-' || id[14] != '4' || !strings.ContainsRune("89ab", rune(id[19])) || label == "" || revision == "" || body["continuity"] != "matched" || scope["registry_revision"] != revision || scope["enrollment_revision"] == nil {
						t.Fatal("known speaker UUID, label or dual evidence binding missing")
					}
					if returningUUID != "" && returningUUID != id {
						t.Fatal("same recorded speaker changed UUID between utterances")
					}
					returningUUID = id
				}
			case "input_finished":
				completions++
				if int64(body["end_sample"].(float64)) != int64(len(sourcePCM)/2) {
					t.Fatal("input cutoff differs")
				}
				faultCheck(t, ap.Voice.Load().CloseFor(ctx, "recorded-continuity", "drain", "recorded capture complete"))
			case "session_end":
				closed = true
			}
		case <-ctx.Done():
			t.Fatal("recorded capture did not retire", ctx.Err())
		}
	}
	if starts < 2 || len(finals) < 2 || completions != 1 || len(finals) != len(observations) {
		t.Fatalf("capture census starts=%d finals=%d observations=%d completions=%d", starts, len(finals), len(observations), completions)
	}
	for seq := range finals {
		if !observations[seq] {
			t.Fatal("final has no matching speaker observation")
		}
	}
	select {
	case <-released:
	case <-ctx.Done():
		t.Fatal("capture pin did not release")
	}
	select {
	case <-binding.Released():
	default:
		t.Fatal("capture endpoints retained")
	}
	if len(sink.PCM()) != 0 {
		t.Fatal("meeting emitted audio")
	}
	t.Logf("recorded capture samples=%d starts=%d finals=%d exact_observation_joins=%d zero_synthesis=true accuracy_qualified=false", len(sourcePCM)/2, starts, len(finals), len(observations))
	if identify {
		t.Logf("installed recorded speaker matches=%d live_identity_unchanged=true broad_accuracy_qualified=false", len(observations))
	}
}
