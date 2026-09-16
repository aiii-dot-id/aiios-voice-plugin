// Go -overlay integration: actual host app/session/dashboard + SDK + MLX.
// Only package activation access, recorded input and the LLM reply are fixtures.
package app

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-os/internal/audio"
	"github.com/aiii-dot-id/aii-os/internal/dashboard"
	"github.com/aiii-dot-id/aii-os/internal/pluginhost"
	"github.com/aiii-dot-id/aii-os/internal/supervisor"
	"github.com/coder/websocket"
)

func TestRealBrowserSDKSpeechInterruptionRecoveryAndDrain(t *testing.T) {
	root, out := os.Getenv("VOICE_ENGINE_PROOF_ROOT"), os.Getenv("VOICE_HOST_PROOF_OUTPUT")
	if root == "" || out == "" {
		t.Fatal("explicit engine root/evidence directory required")
	}
	candidate := os.Getenv("VOICE_SDK_CANDIDATE")
	if candidate == "" {
		candidate = "interrupt-candidate-r2"
	}
	var sdkRevision, correction string
	hostRevision := "4b2753a34c57ff2e4589ea10af3585c2677943e5"
	hostPath := ".build/aii-os-4b2753a-browser-r1"
	switch candidate {
	case "interrupt-candidate-r2":
		sdkRevision, correction = "8d4826485c82d3333faace8129560175ef67608e", "isolated response-verdict custody r2; not landed"
	case "interrupt-candidate-r3":
		sdkRevision, correction = "126476481358e54cda86a509ad2b82b0848b9b71", "isolated bounded reader/admission/writer retirement r3; not landed"
	case "native-sdk-5c64d7c":
		sdkRevision, correction = "5c64d7cb511d9b4964ce1789ae6b88d53cffd258", "landed Control.Answer ownership and publication fence"
		hostRevision, hostPath = "67e07783444f3e9fe29f30f1f6c466357713cacf", ".build/aii-os-67e0778-browser-r1"
	case "native-directml-20260909-r1":
		sdkRevision, correction = "5c64d7cb511d9b4964ce1789ae6b88d53cffd258", "landed Control.Answer with private native DirectML recognizer"
		hostRevision, hostPath = "10423f8702bc64e2bc67033f75b69aac54955a78", ".build/aii-os-10423f8-browser-r1"
	default:
		t.Fatal("unknown explicitly bound SDK candidate")
	}
	candidatePath := filepath.Join(".build", candidate)
	if candidate == "native-directml-20260909-r1" {
		candidatePath = ".build/proof-carrier"
	}
	sdkPath, nativePath := filepath.Join(candidatePath, "sdk"), filepath.Join(candidatePath, "native")
	if candidate == "native-sdk-5c64d7c" || candidate == "native-directml-20260909-r1" {
		sdkPath, nativePath = ".build/aii-plugin-sdk-5c64d7c", "plugin/native"
	}
	backend, carrierName := "mlx", "aii-voice-t3-race"
	python := "/home/user/work/mlx-quant-sota/.venv-311/bin/python"
	browserPath := "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
	expectedReplySamples := 115200 // independent preceding Mac full-reply proof
	workerArgs := []string{"-m", "tests.plugin_real_delayed_ack_fixture", "--root", root}
	pythonPath := root
	if runtime.GOOS == "windows" {
		backend, carrierName = "windows-pocket", "aii-voice-t3.exe"
		python, browserPath = os.Getenv("VOICE_PROOF_PYTHON"), os.Getenv("VOICE_PROOF_BROWSER")
		stage := os.Getenv("VOICE_PROOF_STAGE")
		if python == "" || browserPath == "" || stage == "" {
			t.Fatal("Windows requires explicit interpreter/browser/stage paths")
		}
		expectedReplySamples = 86400 // preceding source-bound Windows SDK recovery WAV
		workerArgs = append(workerArgs, "--stage", stage, "--pocket-root", filepath.Join(stage, "pocket-r1"))
		if candidate == "native-directml-20260909-r1" {
			nativeRoot := os.Getenv("VOICE_NATIVE_STT_ROOT")
			if nativeRoot == "" {
				t.Fatal("native candidate requires an explicit verified recognizer root")
			}
			workerArgs = append(workerArgs, "--native-stt-root", nativeRoot, "--native-stt-python", python)
		}
		pythonPath = strings.Join([]string{root, filepath.Join(stage, "tts-deps"), filepath.Join(stage, "tts", "Qwen3-TTS"), filepath.Join(stage, "runtime", "Lib", "site-packages")}, string(os.PathListSeparator))
	} else if runtime.GOOS != "darwin" {
		t.Fatal("no bound browser/backend profile for this platform")
	}
	if candidate == "native-directml-20260909-r1" && runtime.GOOS != "windows" {
		t.Fatal("native DirectML profile is Windows only")
	}
	workerArgs = append(workerArgs, "--backend", backend)
	withhold := os.Getenv("VOICE_WITHHOLD_TERMINAL_RECEIPT")
	if withhold != "" && withhold != "1" {
		t.Fatal("unknown missing-receipt fixture setting")
	}
	if err := os.MkdirAll(out, 0700); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	report := map[string]any{"scope": "real browser voice module/Web Audio, dashboard WebSocket, app voice path and SDK/carrier/real models; recorded input and fixed LLM reply, not physical audio or packaged containment", "platform": runtime.GOOS, "backend": backend, "expected_reply_samples": expectedReplySamples, "withheld_terminal_receipt": withhold == "1", "host_revision": hostRevision, "sdk_base_revision": sdkRevision, "sdk_correction": correction, "started_utc": time.Now().UTC().Format(time.RFC3339Nano)}
	report["candidate_profile"] = candidate
	hashes := map[string]string{}
	for _, path := range []string{
		"plugin/integration/browser_conversation_test.go", "plugin/integration/browser_conversation.js", "plugin/integration/browser_host_bind.go",
		"tests/plugin_real_delayed_ack_fixture.py", "tests/plugin_delayed_ack_fixture.py",
		filepath.Join(candidatePath, carrierName), filepath.Join(sdkPath, "pkg/aiiosdk/session.go"),
		filepath.Join(nativePath, "main.go"), "runtime/plugin_engine/worker.py", "runtime/plugin_engine/session.py",
		"runtime/stt/cuda_resident.py", "runtime/windows_voice/pocket.py",
		"runtime/cuda_voice/stt.py", "runtime/stt/native_resident.py", "runtime/stt/nemotron_onnx.py",
		"runtime/stt/native_async.py", "runtime/stt/native_streaming.py", "runtime/stt/nemotron_fbank.py",
		filepath.Join(hostPath, "internal/dashboard/static/voice.js"),
		filepath.Join(hostPath, "internal/dashboard/voice_stream.go"),
		filepath.Join(hostPath, "internal/app/voice_session.go"),
		filepath.Join(hostPath, "internal/pluginhost/voicesession.go"),
		filepath.Join(hostPath, "internal/audio/audio.go"),
		filepath.Join(hostPath, "internal/supervisor/supervisor.go"),
		filepath.Join(hostPath, "internal/supervisor/audiopair.go"),
		filepath.Join(hostPath, "internal/supervisor/audiopair_windows.go"),
		filepath.Join(hostPath, "internal/supervisor/audiopair_unix.go"),
	} {
		raw, err := os.ReadFile(filepath.Join(root, path))
		if err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(raw)
		hashes[path] = hex.EncodeToString(h[:])
		captured := filepath.Join(out, "executed-source", path)
		if err := os.MkdirAll(filepath.Dir(captured), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(captured, raw, 0600); err != nil {
			t.Fatal(err)
		}
	}
	report["source_sha256"] = hashes
	t.Cleanup(func() {
		report["passed"] = !t.Failed()
		raw, err := json.MarshalIndent(report, "", "  ")
		if err != nil {
			t.Error(err)
			return
		}
		if err = os.WriteFile(filepath.Join(out, "report.json"), append(raw, '\n'), 0600); err != nil {
			t.Error(err)
		}
	})

	started := time.Now()
	workerEnv := []string{"PYTHONPATH=" + pythonPath, "HF_HUB_OFFLINE=1", "AII_TEST_ACK_DELAY_MS=750", "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1"}
	if candidate == "native-directml-20260909-r1" {
		workerEnv = append(workerEnv, "AII_TEST_MODEL_EVIDENCE="+filepath.Join(out, "model-identity.json"))
		if cpuRoot := os.Getenv("VOICE_TEST_CPU_TORCH"); cpuRoot != "" {
			workerEnv = append(workerEnv, "VOICE_TEST_CPU_TORCH="+cpuRoot)
			report["cpu_dependency_candidate"] = cpuRoot
		}
	}
	sup, err := supervisor.Start(supervisor.Spec{
		PluginID: "voice.browser-real-engine-proof", SessionMode: true, AudioPair: true,
		Argv:      append([]string{filepath.Join(root, candidatePath, carrierName), python}, workerArgs...),
		Env:       workerEnv,
		ReadyMark: "AII_VOICE_READY event=ready", ReadyTimeout: 60 * time.Second,
		Backoff: supervisor.Backoff{Initial: time.Hour, Max: time.Hour, MaxRestarts: 1},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	report["cold_ready_seconds"], report["ready_line"], report["pid"] = time.Since(started).Seconds(), sup.ReadyLine(), sup.Pid()
	t.Cleanup(func() {
		c, done := context.WithTimeout(context.Background(), 15*time.Second)
		defer done()
		err := sup.CloseContext(c)
		report["close_error"], report["restarts"] = fmt.Sprint(err), sup.Restarts()
		if err != nil {
			t.Error(err)
		}
	})
	ap, unbind, err := pluginhost.VoiceBrowserProofBind(sup)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(unbind)
	a := newVoiceApp(t)
	a.plugins = []*pluginhost.ActivePlugin{ap}
	stubWake(t, func() (string, error) { return "The recovery reply is complete. Cobalt lantern seventeen.", nil })
	var eventMu sync.Mutex
	var events []dashboard.VoiceEvent
	var firstPump *audio.Pump
	var pumpMu sync.Mutex
	h := &dashboard.WSHandler{GetStats: func() (*dashboard.StatsResponse, error) { return &dashboard.StatsResponse{}, nil }, AudioPlane: a.AudioPlane, VoiceEngine: a.VoiceEngine,
		VoiceSessionOpen: func(ctx context.Context, in, out, mode string) (dashboard.VoiceSession, error) {
			sess, err := a.OpenVoiceSession(ctx, in, out, mode)
			if err == nil {
				_, p := ap.Voice.Audio()
				pumpMu.Lock()
				if firstPump == nil {
					firstPump = p
				}
				pumpMu.Unlock()
			}
			return sess, err
		},
	}
	s := dashboard.New("127.0.0.1", 0, h)
	a.voiceEventSink = func(ev dashboard.VoiceEvent) {
		eventMu.Lock()
		events = append(events, ev)
		eventMu.Unlock()
		s.BroadcastVoiceEvent(ev)
	}
	a.voiceReplySink = s.BroadcastVoiceReply
	// Loopback-only fixture, matching the plain HTTP reverse proxy below.
	addr, err := s.Start("")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		c, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		if err := s.Shutdown(c); err != nil {
			t.Error(err)
		}
	})
	t.Cleanup(func() {
		c, done := context.WithTimeout(context.Background(), 10*time.Second)
		defer done()
		if ap.Voice.IsOpen() {
			_ = ap.Voice.Close(c, "abort", "proof cleanup")
		}
	})

	wav, err := os.ReadFile(filepath.Join(root, "deliverables/speech-output/validation-20260907-r2/recovery.wav"))
	if err != nil {
		t.Fatal(err)
	}
	src, err := audio.NewFileSource(bytes.NewReader(wav), audio.Format{}, 960)
	if err != nil {
		t.Fatal(err)
	}
	rs := audio.NewResampler(src.Format(), audio.Format{Rate: 48000, Channels: 1})
	var pcm []byte
	for {
		fr, err := src.Read(ctx)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if fr.Kind == audio.KindPCM {
			rs.Feed(fr.PCM)
			pcm = append(pcm, rs.Take()...)
		}
		if fr.Kind == audio.KindEnd {
			pcm = append(pcm, rs.Finish()...)
			break
		}
	}
	inputHash := sha256.Sum256(pcm)
	report["input_pcm_sha256"], report["input_samples"] = hex.EncodeToString(inputHash[:]), len(pcm)/2

	result := make(chan json.RawMessage, 1)
	script := filepath.Join(root, "plugin/integration/browser_conversation.js")
	if override := os.Getenv("VOICE_BROWSER_SCRIPT"); override != "" {
		script = override
	}
	scriptBytes, err := os.ReadFile(script)
	if err != nil {
		t.Fatal(err)
	}
	scriptHash := sha256.Sum256(scriptBytes)
	report["executed_browser_script_sha256"] = hex.EncodeToString(scriptHash[:])
	if err := os.WriteFile(filepath.Join(out, "executed-browser.js"), scriptBytes, 0600); err != nil {
		t.Fatal(err)
	}
	u, _ := url.Parse("http://" + addr)
	proxy := httputil.NewSingleHostReverseProxy(u)
	oldDirector := proxy.Director
	proxy.Director = func(r *http.Request) {
		oldDirector(r)
		r.Host = u.Host
		if r.Header.Get("Origin") != "" {
			r.Header.Set("Origin", u.String())
		}
	}
	page := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/__page":
			w.Header().Set("Content-Type", "text/html")
			contract, _ := json.Marshal(map[string]any{"expectedReplySamples": expectedReplySamples, "withholdTerminalReceipt": withhold == "1", "backend": backend})
			fmt.Fprintf(w, `<!doctype html><html><body><div id="voice-transcript"></div><script>window.proofContract=%s;</script><script type="module" src="/__joint.js"></script></body></html>`, contract)
		case "/__joint.js":
			w.Header().Set("Content-Type", "text/javascript")
			w.Write(scriptBytes)
		case "/app.js":
			w.Header().Set("Content-Type", "text/javascript")
			fmt.Fprint(w, `window.proofToasts=[]; export function toast(s){ window.proofToasts.push(s); }`)
		case "/__input":
			w.Header().Set("Content-Type", "application/octet-stream")
			w.Write(pcm)
		case "/__readback":
			c, done := context.WithTimeout(r.Context(), 5*time.Second)
			defer done()
			snapshot, err := ap.Voice.Status(c)
			if err != nil {
				http.Error(w, err.Error(), 500)
				return
			}
			outputs := map[uint32]audio.OutputStream{}
			pumpMu.Lock()
			p := firstPump
			pumpMu.Unlock()
			if p != nil {
				for id := uint32(1); id <= 4; id++ {
					if st, ok := p.OutputStream(id); ok {
						outputs[id] = st
					}
				}
			}
			// The app now owns Telemetry. The fixture must not compete for
			// observations; prove delivery from the actual browser events.
			readback := map[string]any{"snapshot": snapshot, "outputs": outputs, "telemetry_consumer": "application", "telemetry_dropped": ap.Voice.TelemetryDropped()}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(readback)
		case "/__speak":
			var vh *voiceHandle
			a.voiceSessions.Range(func(_, val any) bool { vh = val.(*voiceHandle); return false })
			if vh == nil {
				http.Error(w, "no session", 409)
				return
			}
			gen := vh.gen.Load()
			vh.work.Add(1)
			go func() {
				defer vh.work.Done()
				a.synthesizeReply(ctx, vh.id, gen, strings.Repeat("This is a longer reply. Please interrupt this sentence and keep the opening words in the next turn. ", 8))
			}()
			w.WriteHeader(http.StatusAccepted)
		case "/__result":
			raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
			if err != nil {
				http.Error(w, err.Error(), 400)
				return
			}
			select {
			case result <- raw:
			default:
			}
			w.WriteHeader(http.StatusNoContent)
		default:
			proxy.ServeHTTP(w, r)
		}
	}))
	defer page.Close()
	profile := t.TempDir()
	logfile, err := os.Create(filepath.Join(out, "chrome.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer logfile.Close()
	chrome := exec.CommandContext(ctx, browserPath, "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-extensions", "--autoplay-policy=no-user-gesture-required", "--mute-audio", "--remote-debugging-port=0", "--user-data-dir="+profile, page.URL+"/__page")
	chrome.Stdout, chrome.Stderr = logfile, logfile
	if err := chrome.Start(); err != nil {
		t.Fatal(err)
	}
	browserDone := make(chan error, 1)
	go func() { browserDone <- chrome.Wait() }()
	defer func() {
		// Ask this exact private-profile browser to close its process family.
		// Killing only the root does not prove Windows renderer retirement.
		c, done := context.WithTimeout(context.Background(), 5*time.Second)
		defer done()
		raw, err := os.ReadFile(filepath.Join(profile, "DevToolsActivePort"))
		parts := strings.Fields(string(raw))
		if err == nil && len(parts) == 2 {
			port, parseErr := strconv.Atoi(parts[0])
			if parseErr == nil && port > 0 && port < 65536 && strings.HasPrefix(parts[1], "/devtools/browser/") {
				ws, _, dialErr := websocket.Dial(c, "ws://127.0.0.1:"+parts[0]+parts[1], nil)
				if dialErr == nil {
					err = ws.Write(c, websocket.MessageText, []byte(`{"id":1,"method":"Browser.close"}`))
					ws.CloseNow()
				} else {
					err = dialErr
				}
			} else {
				err = fmt.Errorf("invalid private browser endpoint")
			}
		}
		select {
		case waitErr := <-browserDone:
			report["browser_close_error"] = fmt.Sprint(waitErr)
			if waitErr != nil {
				t.Error("browser retirement:", waitErr)
			}
		case <-c.Done():
			report["browser_close_error"] = fmt.Sprintf("graceful retirement unproven: %v", err)
			_ = chrome.Process.Kill()
			<-browserDone
			t.Error("browser required forced retirement")
		}
	}()
	select {
	case raw := <-result:
		if err := os.WriteFile(filepath.Join(out, "browser.json"), raw, 0600); err != nil {
			t.Fatal(err)
		}
		var browser struct {
			Passed bool   `json:"passed"`
			Error  string `json:"error"`
		}
		if err := json.Unmarshal(raw, &browser); err != nil {
			t.Fatal(err)
		}
		report["browser"] = json.RawMessage(raw)
		if !browser.Passed {
			t.Errorf("joint browser proof: %s", browser.Error)
		}
	case <-ctx.Done():
		t.Error("browser result missing: ", ctx.Err())
	}
	voiceWait(t, "disconnected page's endpoints released", 10*time.Second, func() bool { return len(a.AudioPlane().Endpoints()) == 0 })
	eventMu.Lock()
	report["events"] = append([]dashboard.VoiceEvent(nil), events...)
	eventMu.Unlock()
	report["endpoints_remaining"] = len(a.AudioPlane().Endpoints())
	report["lane_fault"], report["fault_reason"], report["stale_replies"] = ap.Voice.Faulted(), ap.Voice.FaultReason(), a.voiceStaleReplies.Load()
	if ap.Voice.Faulted() {
		t.Error(ap.Voice.FaultReason())
	}
}
