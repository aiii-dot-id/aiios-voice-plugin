package main

import (
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func TestWaveformToolRecordsLatestFinalWithoutArmOrExtraConfirmation(t *testing.T) {
	if err := validateWaveform("recording.record", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{
		`{"max_seconds":10}`, `{"path":"/tmp/voice.wav"}`,
		`{"audio_b64":"abc"}`, `{"session_id":"guessed"}`,
	} {
		if err := validateWaveform("recording.record", aiiosdk.Object(raw)); err == nil {
			t.Fatalf("unsafe waveform request accepted: %s", raw)
		}
	}
	if err := validateWaveform("recording.status", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal(err)
	}
	if err := validateWaveform("recording.status", aiiosdk.Object(`{"recording_id":"guess"}`)); err == nil {
		t.Fatal("status accepted caller-selected recording")
	}
	if err := validateWaveform("recording.list", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal(err)
	}
	if err := validateWaveform("recording.prune", aiiosdk.Object(`{}`)); err == nil {
		t.Fatal("unconfirmed private cleanup admitted")
	}
	if err := validateWaveform("recording.prune", aiiosdk.Object(`{"path":"uid/.speakers-any.pending"}`)); err == nil {
		t.Fatal("cleanup accepted caller-selected path")
	}
	id := strings.Repeat("a", 64)
	now := time.Now().UTC()
	stamp := `"_host_now_ms":` + strconv.FormatInt(now.UnixMilli(), 10) + `,"_host_operator_act":{"id":"act-1","confirmed_at":"` + now.Format(time.RFC3339Nano) + `"}`
	if err := validateWaveform("recording.prune", aiiosdk.Object(`{`+stamp+`}`)); err != nil {
		t.Fatal(err)
	}
	if err := validateWaveform("recording.delete", aiiosdk.Object(`{"recording_id":"`+id+`",`+stamp+`}`)); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{`{}`, `{"recording_id":"../escape"}`, `{"recording_id":"` + id + `","path":"other"}`} {
		if err := validateWaveform("recording.delete", aiiosdk.Object(raw)); err == nil {
			t.Fatalf("unsafe deletion admitted: %s", raw)
		}
	}
	descriptors := declaredPlugin().Descriptors()
	seen := 0
	for _, d := range descriptors {
		if !waveformOperation(d.ID) {
			continue
		}
		seen++
		if d.OperatorConfirms != (d.ID == "recording.delete" || d.ID == "recording.prune") || len(d.Capabilities) != 1 || d.Capabilities[0] != "fs.private" {
			t.Fatal("recording unexpectedly demands confirmation or lacks private storage", d)
		}
		for _, ref := range []string{d.Input, d.Output} {
			if !strings.HasPrefix(ref, "schemas/recording") {
				t.Fatal("undiscoverable waveform schema", ref)
			}
		}
	}
	if seen != 5 {
		t.Fatalf("expected five waveform operations; saw %d", seen)
	}
}

func TestWaveformBrokerPathIsFixedAndBounded(t *testing.T) {
	id := strings.Repeat("a", 64)
	q := snapshotQuery{settingsQuery: settingsQuery{ID: 1, SessionID: "s"}, Resource: "waveform:" + id,
		Action: "publish", Upload: id, SHA: strings.Repeat("b", 64), Absent: true}
	if !q.valid() || q.target() != "recordings/"+id+".wav" || q.limit() != 1<<20 {
		t.Fatal("waveform private target or limit changed")
	}
	q.Resource = "waveform:../../escape"
	if q.valid() {
		t.Fatal("caller-shaped waveform path accepted")
	}
	q.Resource = "waveform:" + id
	q.Action = "stage"
	q.SHA = ""
	q.Data = "YQ=="
	q.Absent = false
	if !q.valid() || q.target() != "recordings/."+id+".pending" {
		t.Fatal("waveform staging path unbound")
	}
	q.Append = true
	if !q.valid() {
		t.Fatal("second and later waveform upload pages refused")
	}
	q.Append = false
	q.Upload = strings.Repeat("d", 64)
	if q.valid() {
		t.Fatal("waveform resource and staging identity differed")
	}
	q.Upload = id
	q.Action = "publish"
	q.Data = ""
	q.SHA = strings.Repeat("b", 64)
	q.Absent = true
	raw := `{"status":"succeeded","operation_result":{"root":"private","path":"` + q.target() + `","size":320044,"sha256":"` + q.SHA + `","replaced":false,"durable":true,"durability":"synced"}}`
	if _, err := snapshotValue([]byte(raw), q); err != nil {
		t.Fatal(err)
	}
	q.Absent = false
	q.Expected = strings.Repeat("c", 64)
	if q.valid() {
		t.Fatal("waveform replacement accepted")
	}
}
