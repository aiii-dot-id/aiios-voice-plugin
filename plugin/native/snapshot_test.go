package main

import (
	"bufio"
	"context"
	"encoding/json"
	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
	"io"
	"strings"
	"testing"
	"time"
)

func TestSnapshotEnvelopeIsNotAbsence(t *testing.T) {
	q := snapshotQuery{settingsQuery: settingsQuery{1, "s"}, Digest: true}
	for _, raw := range []string{`{}`, `{"status":"failed","reason_code":"FS_NOT_FOUND"}`, `{"status":"succeeded","operation_result":{}}`,
		`{"status":"succeeded","operation_result":{"root":"private","path":"uid/enrollment.json","offset":0,"size":0,"bytes":0,"eof":true,"data_b64":""}}`} {
		if _, err := snapshotValue([]byte(raw), q); err == nil {
			t.Fatalf("failure became empty enrollment: %s", raw)
		}
	}
	good := `{"status":"succeeded","operation_result":{"root":"private","path":"uid/enrollment.json","offset":0,"size":1,"bytes":1,"eof":true,"data_b64":"YQ==","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`
	if _, err := snapshotValue([]byte(good), q); err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	for _, key := range []string{"root", "path", "offset", "size", "bytes", "eof", "data_b64", "sha256"} {
		if err := json.Unmarshal([]byte(good), &body); err != nil {
			t.Fatal(err)
		}
		delete(body["operation_result"].(map[string]any), key)
		raw, _ := json.Marshal(body)
		if _, err := snapshotValue(raw, q); err == nil {
			t.Fatalf("missing %s accepted", key)
		}
	}
}
func TestSnapshotWaitDoesNotHoldInterruption(t *testing.T) {
	for _, resource := range []string{"", "captures"} {
		t.Run("resource="+resource, func(t *testing.T) { snapshotInterruption(t, resource) })
	}
}
func snapshotInterruption(t *testing.T, resource string) {
	c, requests, replies := privateFixture(t)
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		c.readSnapshot(snapshotQuery{settingsQuery: settingsQuery{1, "first"}, Resource: resource}, func(ctx context.Context) (aiiosdk.Object, error) {
			close(entered)
			select {
			case <-release:
				return aiiosdk.Object(`{"status":"failed"}`), nil
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		})
	}()
	t.Cleanup(func() { c.fail(io.EOF); <-done })
	<-entered
	stop := pendingResult(t, c, "stop_playback")
	scan := bufio.NewScanner(requests)
	if !scan.Scan() {
		t.Fatal("stop not delivered")
	}
	var request privateRequest
	if json.Unmarshal(scan.Bytes(), &request) != nil || request.Operation != "stop_playback" {
		t.Fatalf("wrong control %s", scan.Bytes())
	}
	if _, err := io.WriteString(replies, `{"id":1,"result":{"fenced":true}}`+"\n"); err != nil {
		t.Fatal(err)
	}
	if answer := awaitResult(t, stop); answer.Err != nil {
		t.Fatal(answer.Err)
	}
	close(release)
	if !scan.Scan() {
		t.Fatal("snapshot result missing")
	}
	if json.Unmarshal(scan.Bytes(), &request) != nil || request.Snapshot == nil || request.Snapshot.SessionID != "first" || request.Snapshot.Error == "" {
		t.Fatalf("lost snapshot refusal %s", scan.Bytes())
	}
}
func TestPendingCaptureStorageIsFixedAndIsolated(t *testing.T) {
	hash := strings.Repeat("a", 64)
	q := snapshotQuery{settingsQuery: settingsQuery{1, "s"}, Resource: "captures", Digest: true}
	if !q.valid() || q.target() != pendingCapturesPath || q.limit() != 65536 {
		t.Fatal("pending capture target or byte bound differs")
	}
	good := `{"status":"succeeded","operation_result":{"root":"private","path":"uid/captures.json","offset":0,"size":1,"bytes":1,"eof":true,"data_b64":"YQ==","sha256":"` + hash + `"}}`
	if _, err := snapshotValue([]byte(good), q); err != nil {
		t.Fatal(err)
	}
	for _, resource := range []string{"uid/captures.json", "../../other", "enrollment", "Captures"} {
		bad := q
		bad.Resource = resource
		if bad.valid() {
			t.Fatalf("caller-selected resource accepted: %q", resource)
		}
		if _, err := snapshotValue([]byte(good), bad); err == nil {
			t.Fatal("invalid query consumed host receipt")
		}
	}
	profile := q
	profile.Resource = ""
	if _, err := snapshotValue([]byte(good), profile); err == nil {
		t.Fatal("pending evidence mistaken for enrolled identity")
	}
	wrong := strings.Replace(good, "uid/captures.json", "uid/enrollment.json", 1)
	if _, err := snapshotValue([]byte(wrong), q); err == nil {
		t.Fatal("profile read satisfied pending evidence read")
	}
	oversize := strings.Replace(good, `"size":1`, `"size":65537`, 1)
	if _, err := snapshotValue([]byte(oversize), q); err == nil {
		t.Fatal("capture read exceeds private bound")
	}
	q.Digest = false
	q.Action = "stage"
	q.Upload = hash
	q.Data = "YQ=="
	if !q.valid() || q.target() != "uid/.captures-"+hash+".pending" {
		t.Fatal("capture staging namespace not isolated")
	}
	stage := `{"status":"succeeded","operation_result":{"root":"private","path":"` + q.target() + `","size":1,"bytes":1}}`
	if _, err := snapshotValue([]byte(stage), q); err != nil {
		t.Fatal(err)
	}
	if _, err := snapshotValue([]byte(strings.Replace(stage, ".captures-", ".enrollment-", 1)), q); err == nil {
		t.Fatal("enrollment staging receipt accepted for capture")
	}
	q.Action = "publish"
	q.Data = ""
	q.SHA = hash
	q.Absent = true
	op, args := q.call()
	if !q.valid() || op != "fs.publish" || args["from"] != "uid/.captures-"+hash+".pending" || q.target() != pendingCapturesPath {
		t.Fatal("capture publication crosses profile authority")
	}
	published := `{"status":"succeeded","operation_result":{"root":"private","path":"uid/captures.json","size":1,"sha256":"` + hash + `","replaced":false,"durable":true,"durability":"synced"}}`
	if _, err := snapshotValue([]byte(published), q); err != nil {
		t.Fatal(err)
	}
	if _, err := snapshotValue([]byte(strings.Replace(published, "uid/captures.json", "uid/enrollment.json", 1)), q); err == nil {
		t.Fatal("profile publication satisfied capture commit")
	}
}
func TestSnapshotWaitCancelledOnRetirement(t *testing.T) {
	c, _, _ := privateFixture(t)
	entered, done := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		c.readSnapshot(snapshotQuery{settingsQuery: settingsQuery{1, "s"}}, func(ctx context.Context) (aiiosdk.Object, error) { close(entered); <-ctx.Done(); return nil, ctx.Err() })
	}()
	<-entered
	c.fail(io.EOF)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("snapshot wait survived carrier")
	}
}
