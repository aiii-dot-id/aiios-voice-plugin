package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func hostEnrollmentArgs(op string) map[string]any {
	// Deliberately not the guest's wall clock. Only the host time is authority.
	now := time.Date(2025, 1, 2, 3, 4, 5, 0, time.UTC)
	a := map[string]any{"_host_now_ms": now.UnixMilli(), "_host_future": map[string]any{"opaque": true}}
	if op != "speaker.list" {
		a["session_id"] = "s"
		a["_host_operator_act"] = map[string]any{"id": "confirmed-test-act", "confirmed_at": now.Format(time.RFC3339)}
	}
	if op == "speaker.enroll" || op == "speaker.remove" {
		a["speaker_id"] = "public-fixture"
	}
	if op == "speaker.enroll" {
		a["label"] = "Public fixture"
		a["finals"] = []int{1, 2, 3}
	}
	return a
}

func enrollmentObject(a map[string]any) aiiosdk.Object {
	raw, err := json.Marshal(a)
	if err != nil {
		panic(err)
	}
	return aiiosdk.Object(raw)
}

func TestEnrollmentAcceptsHostReservedMetadata(t *testing.T) {
	for _, op := range []string{"speaker.list", "speaker.enroll", "speaker.remove", "speaker.reset"} {
		t.Run(op, func(t *testing.T) {
			a := hostEnrollmentArgs(op)
			if err := validateEnrollment(op, enrollmentObject(a)); err != nil {
				t.Fatalf("real host metadata stranded %s: %v", op, err)
			}
			a["unexpected"] = true
			if err := validateEnrollment(op, enrollmentObject(a)); err == nil || !strings.Contains(err.Error(), "unknown enrollment argument") {
				t.Fatalf("non-host typo was not refused: %v", err)
			}
		})
	}
}

func TestEnrollmentHostMetadataCannotStandInForConfirmation(t *testing.T) {
	for _, damage := range []string{"missing-act", "missing-time", "bad-time", "fractional-time", "overflow-time", "zero-time", "expired", "future", "malformed-act"} {
		t.Run(damage, func(t *testing.T) {
			a := hostEnrollmentArgs("speaker.reset")
			now := a["_host_now_ms"].(int64)
			switch damage {
			case "missing-act":
				delete(a, "_host_operator_act")
			case "missing-time":
				delete(a, "_host_now_ms")
			case "bad-time":
				a["_host_now_ms"] = "yesterday"
			case "fractional-time":
				a["_host_now_ms"] = 1.25
			case "overflow-time":
				a["_host_now_ms"] = uint64(1) << 63
			case "zero-time":
				a["_host_now_ms"] = 0
			case "expired":
				a["_host_now_ms"] = now + 601000
			case "future":
				a["_host_now_ms"] = now - 61000
			case "malformed-act":
				a["_host_operator_act"] = true
			}
			if err := validateEnrollment("speaker.reset", enrollmentObject(a)); err == nil {
				t.Fatal("unconfirmed or invalid host-time write accepted")
			}
		})
	}
}
