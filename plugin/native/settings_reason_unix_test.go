//go:build darwin || linux

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestAFailedSettingsReadReachesTheSessionAsTheWayItFailed(t *testing.T) {
	fixture := os.Getenv("AII_NATIVE_INTERRUPT_FIXTURE")
	if fixture == "" {
		t.Skip("AII_NATIVE_INTERRUPT_FIXTURE names no aii_voice_worker_fixture (-DAII_WORKER_FIXTURE=ON)")
	}
	h := startNativeCarrier(t, fixture)

	outcome := func(session string, within time.Duration) (kind, reason string) {
		t.Helper()
		for deadline := time.Now().Add(within); ; time.Sleep(time.Millisecond) {
			h.mu.Lock()
			for _, raw := range h.events {
				var e struct {
					Type    string `json:"type"`
					Session string `json:"session_id"`
					Reason  string `json:"reason"`
				}
				if json.Unmarshal(raw, &e) == nil && e.Session == session && (e.Type == "failure" || e.Type == "session_ready") {
					kind, reason = e.Type, e.Reason
				}
			}
			h.mu.Unlock()
			if kind != "" {
				return kind, reason
			}
			if time.Now().After(deadline) {
				t.Fatalf("session %s neither opened nor failed in %v", session, within)
			}
		}
	}
	open := func(id int, session string) {
		t.Helper()
		h.control(id, aiiosdk.OpSessionOpen, fmt.Sprintf(`{"session_id":%q,"output_handle":"playback","audio":{"format":"s16le","input":null,"output":{"rate":48000,"channels":2}}}`, session))
		h.await("the open's admission", func() bool { return h.replies[id] != nil })
	}

	wait := defaultLimits.opening()
	for i, way := range []struct {
		session string
		answer  func(id json.RawMessage) string
		within  time.Duration
		said    string
	}{
		{"refused", func(id json.RawMessage) string {
			return fmt.Sprintf(`{"jsonrpc":"2.0","id":%s,"error":{"code":-32000,"message":"refused by the test host"}}`, id)
		}, 10 * time.Second, "host settings: the host refused the read"},
		{"not-settings", func(id json.RawMessage) string {
			return fmt.Sprintf(`{"jsonrpc":"2.0","id":%s,"result":{"status":"succeeded","operation_result":{"values":[]}}}`, id)
		}, 10 * time.Second, "host settings: the host's answer is not settings"},
		{"unanswered", func(json.RawMessage) string { return "" }, wait + 10*time.Second, "host settings: no answer from the host in time"},
	} {
		h.mu.Lock()
		h.settingsAnswer = way.answer
		h.mu.Unlock()
		began := time.Now()
		open(i+1, way.session)
		kind, reason := outcome(way.session, way.within)
		if kind != "failure" || reason != way.said {
			t.Errorf("session %s ended as %s %q; the way its settings read failed is %q", way.session, kind, reason, way.said)
		}
		if waited := time.Since(began); way.session == "unanswered" && waited < wait-time.Second {
			t.Errorf("a read the host never answered was given up after %v; the host's time is %v", waited, wait)
		}
	}

	h.mu.Lock()
	h.settingsAnswer = nil
	h.mu.Unlock()
	open(4, "after")
	if kind, reason := outcome("after", 10*time.Second); kind != "session_ready" {
		t.Fatalf("the engine did not open a session after three failed settings reads: %s %q", kind, reason)
	}
	select {
	case <-h.done:
		t.Fatalf("the carrier ended with a session's failed settings read: %v", h.err)
	default:
	}
}
