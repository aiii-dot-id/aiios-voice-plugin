// This test is injected with Go's -overlay into a frozen AII OS checkout.
// It changes no host production code and uses the real SDK carrier and MLX
// engine. It qualifies output transport, not browser rendering or containment.
package pluginhost

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-os/internal/audio"
	"github.com/aiii-dot-id/aii-os/internal/supervisor"
)

// Keep capture open, but send no synthetic speech: a reply's END must not
// end a resident session that can still receive the next user utterance.
type residentOutputSource struct{}

func (*residentOutputSource) Format() audio.Format { return audio.Format{Rate: 48000, Channels: 1} }
func (*residentOutputSource) Read(ctx context.Context) (audio.Frame, error) {
	<-ctx.Done()
	return audio.Frame{}, ctx.Err()
}

type residentOutputSink struct {
	mu      sync.Mutex
	f       audio.Format
	pcm     map[uint32][]byte
	ends    map[uint32]int64
	closed  bool
	changed chan struct{}
}

func (s *residentOutputSink) Format() audio.Format { return s.f }
func (s *residentOutputSink) signal() {
	select {
	case s.changed <- struct{}{}:
	default:
	}
}
func (s *residentOutputSink) Write(_ context.Context, fr audio.Frame) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return fmt.Errorf("sink already closed")
	}
	if fr.Kind == audio.KindPCM {
		if fr.Start != int64(len(s.pcm[fr.Stream])/2) {
			return fmt.Errorf("noncontiguous output")
		}
		s.pcm[fr.Stream] = append(s.pcm[fr.Stream], fr.PCM...)
	}
	if fr.Kind == audio.KindEnd {
		s.ends[fr.Stream] = fr.Start
	}
	s.signal()
	return nil
}
func (s *residentOutputSink) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	s.signal()
	return nil
}
func (s *residentOutputSink) waitEnd(ctx context.Context, stream uint32) error {
	for {
		s.mu.Lock()
		_, ended := s.ends[stream]
		closed := s.closed
		s.mu.Unlock()
		if ended {
			return nil
		}
		if closed {
			return fmt.Errorf("output sink closed before stream %d END", stream)
		}
		select {
		case <-s.changed:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func TestRealResidentHostSpokenInterruptionAndRecovery(t *testing.T) {
	root := os.Getenv("VOICE_ENGINE_PROOF_ROOT")
	out := os.Getenv("VOICE_HOST_PROOF_OUTPUT")
	if root == "" || out == "" {
		t.Fatal("explicit engine root and evidence directory required")
	}
	for _, rate := range []int{24000, 48000} {
		t.Run(fmt.Sprintf("speaker_%d", rate), func(t *testing.T) {
			dir := filepath.Join(out, fmt.Sprintf("speaker-%d", rate))
			if err := os.MkdirAll(dir, 0700); err != nil {
				t.Fatal(err)
			}
			report := map[string]any{
				"scope":         "real AII OS supervisor/session/audio plane + SDK carrier + MLX engine; no browser, devices or containment claim",
				"host_revision": "45025460734667e2da9c44d9ad44a495bb54b86f+isolated-output-lifetime-overlay",
				"sdk_revision":  "2acd6a0da9537d1cb74523447d6351e2b21cbe8e",
				"speaker_rate":  rate, "started_utc": time.Now().UTC().Format(time.RFC3339Nano),
			}
			hashes := map[string]string{}
			for _, path := range []string{
				".build/aii-os-4502546-integration-r1/internal/audio/audio.go",
				".build/aii-os-4502546-integration-r1/internal/pluginhost/voiceaudio.go",
				".build/aii-os-4502546-integration-r1/internal/pluginhost/voicesession.go",
				".build/aii-voice-t3-race", "plugin/integration/host_resident_conversation_candidate_test.go", "deliverables/speech-output/validation-20260907-r2/recovery.wav",
				"plugin/integration/host_audio_lifetime_candidate.go", "runtime/plugin_engine/session.py", "runtime/plugin_engine/worker.py", "runtime/plugin_engine/readiness.py",
			} {
				b, err := os.ReadFile(filepath.Join(root, path))
				if err != nil {
					t.Fatal(err)
				}
				h := sha256.Sum256(b)
				hashes[path] = hex.EncodeToString(h[:])
			}
			report["source_sha256"] = hashes
			var events []json.RawMessage
			sink := &residentOutputSink{f: audio.Format{Rate: rate, Channels: 1}, pcm: map[uint32][]byte{}, ends: map[uint32]int64{}, changed: make(chan struct{}, 1)}
			// Registered first, runs last: evidence includes bounded cleanup.
			t.Cleanup(func() {
				report["events"] = events
				report["passed"] = !t.Failed()
				sink.mu.Lock()
				streams := map[uint32]any{}
				for stream, pcm := range sink.pcm {
					h := sha256.Sum256(pcm)
					end, hasEnd := sink.ends[stream]
					streams[stream] = map[string]any{"samples": len(pcm) / 2, "end": end, "has_end": hasEnd, "pcm_sha256": hex.EncodeToString(h[:])}
					if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("stream-%d.s16le", stream)), pcm, 0600); err != nil {
						t.Error(err)
					}
				}
				report["sink_streams"], report["sink_closed"] = streams, sink.closed
				sink.mu.Unlock()
				data, err := json.MarshalIndent(report, "", "  ")
				if err != nil {
					t.Error(err)
					return
				}
				if err := os.WriteFile(filepath.Join(dir, "report.json"), append(data, '\n'), 0600); err != nil {
					t.Error(err)
				}
			})
			started := time.Now()
			sup, err := supervisor.Start(supervisor.Spec{
				PluginID: "voice.real-engine-proof", SessionMode: true, AudioPair: true,
				Argv:      []string{filepath.Join(root, ".build/aii-voice-t3-race"), "/home/user/work/mlx-quant-sota/.venv-311/bin/python", "-m", "runtime.plugin_engine.worker", "--root", root, "--backend", "mlx"},
				Env:       []string{"PYTHONPATH=" + root, "HF_HUB_OFFLINE=1"},
				ReadyMark: "AII_VOICE_READY event=ready", ReadyTimeout: 180 * time.Second,
				Backoff: supervisor.Backoff{Initial: time.Hour, Max: time.Hour, MaxRestarts: 1},
			}, nopDispatcher{})
			if err != nil {
				t.Fatal(err)
			}
			report["cold_ready_seconds"], report["ready_line"], report["carrier_pid"] = time.Since(started).Seconds(), sup.ReadyLine(), sup.Pid()
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				err := sup.CloseContext(ctx)
				report["supervisor_close_error"] = fmt.Sprint(err)
				report["supervisor_restarts"] = sup.Restarts()
				if err != nil {
					t.Error(err)
				}
			})
			ap := &ActivePlugin{ID: "voice.real-engine-proof", sup: sup}
			if err := ap.bindVoiceSession(sup); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(ap.sessionCancel)
			ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
			defer cancel()
			plane := audio.NewPlane()
			wav, err := os.ReadFile(filepath.Join(root, "deliverables/speech-output/validation-20260907-r2/recovery.wav"))
			if err != nil {
				t.Fatal(err)
			}
			file, err := audio.NewFileSource(bytes.NewReader(wav), audio.Format{}, 960)
			if err != nil {
				t.Fatal(err)
			}
			f48 := audio.Format{Rate: 48000, Channels: 1}
			rs := audio.NewResampler(file.Format(), f48)
			var pcm []byte
			for {
				fr, err := file.Read(ctx)
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
			source := &pacedResidentSource{pcm: pcm, start: make(chan struct{}), beforeTail: make(chan struct{}), allowTail: make(chan struct{})}
			inputSHA := sha256.Sum256(pcm)
			report["input_source_samples"], report["input_pcm_sha256"] = len(pcm)/2, hex.EncodeToString(inputSHA[:])
			report["input_source_rate"], report["fixture_preparation"] = 48000, "public fixture resampled to browser-rate PCM; host converts 48k to 16k"

			if err := plane.Register(&audio.Endpoint{ID: "mic", Source: source}); err != nil {
				t.Fatal(err)
			}
			if err := plane.Register(&audio.Endpoint{ID: "speaker", Sink: sink}); err != nil {
				t.Fatal(err)
			}
			binding, err := plane.Bind("two-replies", "mic", "speaker", false)
			if err != nil {
				t.Fatal(err)
			}
			if err := ap.Voice.OpenWithAudio(ctx, "two-replies", binding, nil); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				err := ap.Voice.Close(ctx, "abort", "isolated integration proof finished")
				report["session_close_error"] = fmt.Sprint(err)
				if err != nil {
					t.Error(err)
					return
				}
				select {
				case <-binding.Released():
					report["binding_released"] = true
				case <-ctx.Done():
					t.Error("session endpoints not released")
				}
			})
			waitEvent := func(kind, synthesis string) map[string]any {
				for _, raw := range events {
					var body map[string]any
					if err := json.Unmarshal(raw, &body); err != nil {
						t.Fatal(err)
					}
					if body["type"] == kind && (synthesis == "" || body["synthesis_id"] == synthesis) {
						return body
					}
				}
				for {
					select {
					case e, ok := <-ap.Voice.Observe():
						if !ok {
							t.Fatal("host observation lane closed")
						}
						events = append(events, append(json.RawMessage(nil), e.Raw...))
						if e.SessionID != "two-replies" {
							t.Fatalf("foreign session event %s", e.SessionID)
						}
						var body map[string]any
						if err := json.Unmarshal(e.Raw, &body); err != nil {
							t.Fatal(err)
						}
						if e.Type == "failure" {
							t.Fatalf("engine failure %s", e.Raw)
						}
						if e.Type == kind && (synthesis == "" || body["synthesis_id"] == synthesis) {
							return body
						}
					case <-ctx.Done():
						t.Fatalf("no %s for %s: %v", kind, synthesis, ctx.Err())
					}
				}
			}
			waitEvent("session_ready", "")
			for _, sid := range []string{"first-reply", "second-reply"} {
				if err := ap.Voice.Synthesize(ctx, sid, "The local voice service is ready."); err != nil {
					t.Fatal(err)
				}
				end := waitEvent("synthesis_end", sid)
				report[sid] = end
				stream := uint32(end["output_stream"].(float64))
				if end["delivered_samples"].(float64) <= 0 {
					t.Fatal("engine delivered no PCM")
				}
				waitCtx, waitCancel := context.WithTimeout(ctx, 5*time.Second)
				err := sink.waitEnd(waitCtx, stream)
				waitCancel()
				t.Logf("%s engine stream=%d samples=%v host delivery error=%v", sid, stream, end["delivered_samples"], err)
				if err != nil {
					snap, serr := ap.Voice.Status(ctx)
					report["engine_status"], report["status_error"], report["delivery_error"] = snap, fmt.Sprint(serr), err.Error()
					t.Fatalf("resident reply lost between engine and sink: %v", err)
				}
			}
			if err := ap.Voice.Synthesize(ctx, "interrupt-reply", strings.Repeat("This is a longer reply. Please interrupt this sentence and keep the opening words in the next turn. ", 8)); err != nil {
				t.Fatal(err)
			}
			begun := waitEvent("synthesis_start", "interrupt-reply")
			stream := uint32(begun["output_stream"].(float64))
			if err := sink.waitPCM(ctx, stream); err != nil {
				t.Fatal(err)
			}
			feedStart := time.Now()
			close(source.start)
			interrupted := waitEvent("interruption_requested", "interrupt-reply")
			cancelled := waitEvent("synthesis_cancelled", "interrupt-reply")
			report["interruption"], report["cancelled"] = interrupted, cancelled
			report["vad_to_retirement_ms"] = (cancelled["observed_monotonic_ns"].(float64) - interrupted["observed_monotonic_ns"].(float64)) / 1e6
			if cancelled["delivered_samples"].(float64) <= 0 {
				t.Fatal("no active speech was interrupted")
			}
			if err := sink.waitEnd(ctx, stream); err != nil {
				t.Fatal(err)
			}
			select {
			case <-source.beforeTail:
			case <-ctx.Done():
				t.Fatal("source never reached held final packet")
			}
			report["finish_input_before_tail"] = true
			if err := ap.Voice.FinishInput(ctx, "mic", int64(len(pcm)/2)); err != nil {
				t.Fatal(err)
			}
			close(source.allowTail)
			final := waitEvent("transcript_final", "")
			report["transcript_final"], report["feed_to_final_seconds"] = final, time.Since(feedStart).Seconds()
			words := regexp.MustCompile("[a-z0-9]+")
			expected := "Please keep the opening words cobalt lantern seventeen. The recovery reply is now complete."
			got := strings.Join(words.FindAllString(strings.ToLower(final["text"].(string)), -1), " ")
			want := strings.Join(words.FindAllString(strings.ToLower(expected), -1), " ")
			if got != want {
				t.Fatalf("opening/later words changed: %q, want %q", got, want)
			}
			if err := ap.Voice.Synthesize(ctx, "recovery-reply", "The recovery reply is complete. Cobalt lantern seventeen."); err != nil {
				t.Fatal(err)
			}
			recovery := waitEvent("synthesis_end", "recovery-reply")
			report["recovery-reply"] = recovery
			if err := sink.waitEnd(ctx, uint32(recovery["output_stream"].(float64))); err != nil {
				t.Fatal(err)
			}
			snap, err := ap.Voice.Status(ctx)
			if err != nil {
				t.Fatal(err)
			}
			report["final_snapshot"] = snap
			wantInput := int64(len(pcm) / 2 / 3)
			if snap.Input.State != "finished" || snap.Input.AdmittedEndSample != wantInput || snap.Input.ProcessedEndSample != wantInput {
				t.Fatalf("input tail mismatch: %+v expected %d samples", snap.Input, wantInput)
			}
			if snap.Playback.State != "unobserved" {
				t.Fatalf("pipe proof cannot certify playback: %+v", snap.Playback)
			}
			report["closure_scope"] = "abort after successful engine/host delivery; browser render/drain receipt not available"
			t.Logf("all 14 words retained, source=%d engine=%d samples, VAD-to-retirement=%.3fms, recovery reaches host sink",
				len(pcm)/2, wantInput, report["vad_to_retirement_ms"])

		})
	}
}

type pacedResidentSource struct {
	pcm                          []byte
	next                         int
	seq                          uint32
	origin                       time.Time
	start, beforeTail, allowTail chan struct{}
	ended                        bool
}

func (*pacedResidentSource) Format() audio.Format { return audio.Format{Rate: 48000, Channels: 1} }
func (s *pacedResidentSource) Read(ctx context.Context) (audio.Frame, error) {
	if s.ended {
		return audio.Frame{}, io.EOF
	}
	select {
	case <-s.start:
	case <-ctx.Done():
		return audio.Frame{}, ctx.Err()
	}
	if s.origin.IsZero() {
		s.origin = time.Now()
	}
	if s.next == len(s.pcm) {
		s.ended = true
		s.seq++
		return audio.Frame{Kind: audio.KindEnd, Stream: 1, Seq: s.seq, Start: int64(s.next / 2)}, nil
	}
	end := min(s.next+2018, len(s.pcm))
	// Hold the last ragged packet until finish-input admission is observed.
	if end == len(s.pcm) {
		close(s.beforeTail)
		select {
		case <-s.allowTail:
		case <-ctx.Done():
			return audio.Frame{}, ctx.Err()
		}
	}
	timer := time.NewTimer(time.Until(s.origin.Add(time.Duration(s.next/2) * time.Second / 48000)))
	defer timer.Stop()
	select {
	case <-timer.C:
	case <-ctx.Done():
		return audio.Frame{}, ctx.Err()
	}
	s.seq++
	fr := audio.Frame{Kind: audio.KindPCM, Stream: 1, Seq: s.seq, Start: int64(s.next / 2), PCM: s.pcm[s.next:end]}
	s.next = end
	return fr, nil
}
func (s *residentOutputSink) waitPCM(ctx context.Context, stream uint32) error {
	for {
		s.mu.Lock()
		samples, closed := len(s.pcm[stream]), s.closed
		s.mu.Unlock()
		if samples > 0 {
			return nil
		}
		if closed {
			return fmt.Errorf("sink closed before first PCM for %d", stream)
		}
		select {
		case <-s.changed:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}
