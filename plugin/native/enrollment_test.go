package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestEnrollmentRequiresOperatorAndPreservesSpeechDeadline(t *testing.T) {
	now := time.Now().UTC()
	stamp := `"_host_now_ms":` + strconv.FormatInt(now.UnixMilli(), 10) + `,"_host_operator_act":{"id":"act-1","confirmed_at":"` + now.Format(time.RFC3339Nano) + `"}`
	good := aiiosdk.Object(`{"session_id":"s","speaker_id":"person","label":"Chosen","finals":[1,2,3],` + stamp + `}`)
	if err := validateEnrollment("speaker.enroll", good); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{`{"session_id":"s"}`, `{"session_id":"s","_host_operator_act":{"id":"a","confirmed_at":"2000-01-01T00:00:00Z"}}`, `{"session_id":"s","audio":"caller bytes",` + stamp + `}`} {
		if validateEnrollment("speaker.enroll", aiiosdk.Object(raw)) == nil {
			t.Fatalf("unconfirmed/expired/arbitrary input accepted: %s", raw)
		}
	}
	if err := validateEnrollment("speaker.list", aiiosdk.Object(`{"session_id":"s"}`)); err != nil {
		t.Fatal(err)
	}
	if err := validateEnrollment("speaker.list", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal("discovery needs no caller knowledge of a session ID", err)
	}
	p := declaredPlugin()
	if len(p.Operations()) != 14 {
		t.Fatal("enrollment operations absent")
	}
	for _, d := range p.Descriptors() {
		if enrollmentOperation(d.ID) {
			if d.OperatorConfirms != (d.ID != "speaker.list") || len(d.Capabilities) != 1 || d.Capabilities[0] != "fs.private" {
				t.Fatal("wrong operator/capability gate", d)
			}
		}
	}
	c := newCarrier(nil, nil, func() {})
	defer c.cancel()
	for _, op := range []string{"speaker.enroll", "speech.session.stop_playback", "speech.session.cancel_synthesis"} {
		before := time.Now()
		if e := c.enqueue(nil, op, good, func(any, error) {}); e != nil {
			t.Fatal(e)
		}
		delta := c.pending[c.id].deadline.Sub(before)
		if enrollmentOperation(op) {
			if delta < 44*time.Second || delta > 46*time.Second {
				t.Fatal(delta)
			}
		} else if delta < time.Second || delta > 3*time.Second {
			t.Fatal("speech control acquired enrollment deadline", delta)
		}
	}
}
func TestSnapshotPublicationShapeAndAbsence(t *testing.T) {
	hash := strings.Repeat("a", 64)
	q := snapshotQuery{settingsQuery: settingsQuery{ID: 1, SessionID: "s"}, Action: "publish", Upload: hash, SHA: hash, Absent: true}
	if !q.valid() {
		t.Fatal("first CAS refused")
	}
	q.Expected = "bad"
	if q.valid() {
		t.Fatal("malformed expected generation accepted")
	}
	q.Expected = hash
	if q.valid() {
		t.Fatal("both generation modes accepted")
	}
	q.Absent = false
	if !q.valid() {
		t.Fatal("replacement refused")
	}
	q.Upload = "../current"
	if q.valid() {
		t.Fatal("caller staging path accepted")
	}
	q.Upload = hash
	data := base64.StdEncoding.EncodeToString([]byte("abc"))
	q.Action = "stage"
	q.Data = data
	q.SHA = ""
	q.Expected = ""
	if !q.valid() {
		t.Fatal("bounded stage refused")
	}
	op, args := q.call()
	if op != "fs.write" || args["append"] != false || q.target() == snapshotPath {
		t.Fatal("stage targets current snapshot")
	}
	stage := `{"status":"succeeded","operation_result":{"root":"private","path":"` + q.target() + `","bytes":3,"size":3}}`
	if _, e := snapshotValue([]byte(stage), q); e != nil {
		t.Fatal(e)
	}
	q = snapshotQuery{settingsQuery: settingsQuery{ID: 1, SessionID: "s"}}
	missing := `{"status":"failed","reasonCode":"FS_NOT_FOUND"}`
	_, err := snapshotValue([]byte(missing), q)
	var typed snapshotFailure
	if !errors.As(err, &typed) || typed.reason != "FS_NOT_FOUND" {
		t.Fatal("typed absence lost", err)
	}
	for _, raw := range []string{`{"status":"denied","reasonCode":"FS_NOT_FOUND"}`, `{"status":"failed","reasonCode":"FS_IO_FAILED"}`, `{"status":"failed","reasonCode":"FS_NOT_FOUND","reason_code":"FS_IO_FAILED"}`} {
		_, err = snapshotValue([]byte(raw), q)
		if errors.As(err, &typed) {
			t.Fatal("failure became typed absence", raw)
		}
	}
	q.Offset = 1
	_, err = snapshotValue([]byte(missing), q)
	if errors.As(err, &typed) {
		t.Fatal("later missing page became empty")
	}
	encoded, _ := json.Marshal(q)
	if len(encoded) > 4096 {
		t.Fatal("query unexpectedly large")
	}
}
