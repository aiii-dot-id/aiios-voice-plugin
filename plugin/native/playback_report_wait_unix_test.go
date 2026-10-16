//go:build darwin || linux

package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// A PLAYBACK REPORT HELD BEHIND A STALLED AUDIO PIPE DOES NOT FAIL THE CARRIER
// BEFORE THE WORKER SAYS THE PIPE STALLED. The worker holds a report until
// the audio write it counts has ended, and gives that write its time before
// it calls the pipe stalled. The carrier gave every control two seconds,
// typed, and the worker gave a write three, typed: the carrier failed itself
// with "worker admission timeout" a second before the worker could say what
// had happened. The carrier now waits for a report the audio write's time
// and a control's (limits.playbackReport), by the table.
//
// The carrier and the worker's own code, as in native_fixture_unix_test.go,
// under the default table: a development carrier states no other.
func TestAReportHeldBehindAStalledAudioWriteWaitsForTheWorkersOwnDeadline(t *testing.T) {
	fixture := os.Getenv("AII_NATIVE_INTERRUPT_FIXTURE")
	if fixture == "" {
		t.Skip("AII_NATIVE_INTERRUPT_FIXTURE names no aii_voice_worker_fixture (-DAII_WORKER_FIXTURE=ON)")
	}
	h := startNativeCarrier(t, fixture, "AII_FIXTURE_AUDIO_DEADLINE=writer")
	h.control(1, aiiosdk.OpSessionOpen, `{"session_id":"held","output_handle":"playback","audio":{"format":"s16le","input":null,"output":{"rate":48000,"channels":2}}}`)
	h.event("session_ready")
	h.control(2, aiiosdk.OpSessionSynthesize, `{"session_id":"held","synthesis_id":"reply","text":"Flood."}`)
	h.await("synthesis admission", func() bool { return h.replies[2] != nil })
	var admitted struct {
		Stream int `json:"output_stream"`
	}
	h.mu.Lock()
	err := json.Unmarshal(h.replies[2], &admitted)
	h.mu.Unlock()
	if err != nil || admitted.Stream == 0 {
		t.Fatalf("the synthesis reply names no output stream: %v", err)
	}
	var head [28]byte
	if _, err := io.ReadFull(h.audioOut, head[:]); err != nil || string(head[:4]) != "AUD1" {
		t.Fatalf("first audio frame: %v %q", err, head[:4])
	}
	first := int(binary.BigEndian.Uint32(head[24:28])) / 2
	if _, err := io.ReadFull(h.audioOut, make([]byte, 2*first)); err != nil {
		t.Fatal(err)
	} // the host takes one whole frame, then stops reading: the next write stalls
	stalled := time.Now()

	// A report of more samples than the first frame held: only the write
	// that is now stalled could have delivered them, so the worker holds the
	// report until that write is accounted for. Sent before the worker has
	// begun that write it is refused at once; then it is sent again.
	answered := func(id int, within time.Duration) bool {
		for deadline := time.Now().Add(within); time.Now().Before(deadline); time.Sleep(time.Millisecond) {
			h.mu.Lock()
			_, ok := h.replies[id]
			h.mu.Unlock()
			if ok {
				return true
			}
		}
		return false
	}
	report := 3
	for ; ; report++ {
		h.control(report, aiiosdk.OpSessionPlaybackReport, fmt.Sprintf(`{"session_id":"held","synthesis_id":"reply","output_stream":%d,"rendered_samples":%d,"terminal":false}`, admitted.Stream, first+1))
		if !answered(report, 300*time.Millisecond) {
			break
		}
		if time.Since(stalled) > 1500*time.Millisecond {
			t.Fatalf("no report was held: each of %d was answered at once", report-2)
		}
	}
	held := time.Since(stalled)

	// The worker's deadline for the write comes first, and says what happened.
	failure := h.event("failure")
	waited := time.Since(stalled)
	var reason struct {
		Reason string `json:"reason"`
	}
	if json.Unmarshal(failure, &reason) != nil || reason.Reason != "native pipe write expired: 3000 ms, the time the limits table gives it (audio_write_ms)" {
		t.Fatalf("not the audio writer's own deadline: %s", failure)
	}
	if write := defaultLimits.AudioWrite; waited < write-200*time.Millisecond || held >= write {
		t.Fatalf("the report was held %v after the stall and the failure came %v after it; the table gives a write %v", held, waited, write)
	}
	select {
	case <-h.done:
	case <-time.After(6 * time.Second):
		t.Fatal("the carrier stayed up after its worker retired")
	}
	var exit *exec.ExitError
	said := h.stderr.String()
	if !errors.As(h.err, &exit) || exit.ExitCode() != 1 || !strings.Contains(said, "aii-voice-t3: EOF") || strings.Contains(said, "worker admission timeout") {
		t.Fatalf("the carrier did not end on its worker's exit (%v): it gave up on the held report first:\n%s", h.err, said)
	}
	t.Logf("a report held %v after the stall; %s %v after it; the carrier waits %v for a report and %v for any other control",
		held.Round(time.Millisecond), reason.Reason, waited.Round(time.Millisecond), defaultLimits.playbackReport(), defaultLimits.Control)
}
