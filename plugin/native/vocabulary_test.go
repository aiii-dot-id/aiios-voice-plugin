package main

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiospkg"
)

// correctionHost is the private store as the host's broker answers it: read
// with a digest, stage, and a compare-and-swap publish. Every path it is
// asked for is kept, so a test can say exactly which files were touched.
type correctionHost struct {
	files      map[string][]byte
	calls      []string
	durability string // "" answers synced
	deny       string // an operation refused outright
	raceOnce   []byte // written to the list just before the next publish
}

func (h *correctionHost) HostCallTo(_ context.Context, operation string, target any, raw any) (aiiosdk.Object, error) {
	name, ok := target.(map[string]any)
	path, _ := name["path"].(string)
	if !ok || name["root"] != "private" || path == "" {
		return nil, errors.New("foreign private target")
	}
	h.calls = append(h.calls, operation+":"+path)
	if operation == h.deny {
		return aiiosdk.Object(`{"status":"denied","reasonCode":"FS_DENIED"}`), nil
	}
	args, _ := raw.(map[string]any)
	reply := func(result map[string]any) (aiiosdk.Object, error) {
		result["root"], result["path"] = "private", path
		out, err := json.Marshal(map[string]any{"status": "succeeded", "operation_result": result})
		return aiiosdk.Object(out), err
	}
	digest := func(b []byte) string { sum := sha256.Sum256(b); return hex.EncodeToString(sum[:]) }
	switch operation {
	case "fs.read":
		data, present := h.files[path]
		if !present {
			return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_NOT_FOUND"}`), nil
		}
		return reply(map[string]any{"data_b64": base64.StdEncoding.EncodeToString(data), "bytes": len(data), "offset": 0, "size": len(data), "eof": true, "sha256": digest(data)})
	case "fs.write":
		data, err := base64.StdEncoding.DecodeString(args["data_b64"].(string))
		if err != nil {
			return nil, err
		}
		h.files[path] = data
		return reply(map[string]any{"bytes": len(data), "size": len(data)})
	case "fs.publish":
		if h.raceOnce != nil {
			h.files[path], h.raceOnce = h.raceOnce, nil
		}
		staged, from := h.files[args["from"].(string)], args["from"].(string)
		current, present := h.files[path]
		if absent, _ := args["expected_absent"].(bool); absent {
			if present {
				return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_GENERATION_MISMATCH"}`), nil
			}
		} else if !present || digest(current) != args["expected_sha256"] {
			return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_GENERATION_MISMATCH"}`), nil
		}
		if digest(staged) != args["sha256"] {
			return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_DIGEST_MISMATCH"}`), nil
		}
		delete(h.files, from)
		h.files[path] = staged
		durability, durable := "synced", true
		if h.durability != "" {
			durability, durable = h.durability, false
		}
		return reply(map[string]any{"size": len(staged), "sha256": digest(staged), "replaced": present, "durable": durable, "durability": durability})
	}
	return nil, errors.New("operation outside the private store's three")
}

func confirmed(fields map[string]any) aiiosdk.Object {
	now := time.Date(2025, 1, 2, 3, 4, 5, 0, time.UTC)
	fields["_host_now_ms"] = now.UnixMilli()
	fields["_host_operator_act"] = map[string]any{"id": "confirmed-test-act", "confirmed_at": now.Format(time.RFC3339)}
	raw, _ := json.Marshal(fields)
	return aiiosdk.Object(raw)
}

func listed(t *testing.T, value any) (uint64, []correctionRule, map[string]any) {
	t.Helper()
	raw, _ := json.Marshal(value)
	var reply struct {
		Status string `json:"status"`
		Result struct {
			Revision uint64           `json:"revision"`
			Rules    []correctionRule `json:"rules"`
		} `json:"operation_result"`
	}
	var envelope struct {
		Result map[string]any `json:"operation_result"`
	}
	if json.Unmarshal(raw, &reply) != nil || json.Unmarshal(raw, &envelope) != nil || reply.Status != "succeeded" || reply.Result.Rules == nil {
		t.Fatalf("not a succeeded list: %s", raw)
	}
	return reply.Result.Revision, reply.Result.Rules, envelope.Result
}

func TestCorrectionRulesFollowTheSharedVectors(t *testing.T) {
	raw, err := os.ReadFile("../../spec/correction_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors struct {
		Valid   []correctionRule `json:"valid"`
		Invalid []correctionRule `json:"invalid"`
	}
	if json.Unmarshal(raw, &vectors) != nil || len(vectors.Valid) < 8 || len(vectors.Invalid) < 18 {
		t.Fatal("vector file incomplete")
	}
	for _, rule := range vectors.Valid {
		if err := validateCorrection(rule); err != nil {
			t.Errorf("valid rule %q refused: %v", rule.Heard, err)
		}
	}
	for _, rule := range vectors.Invalid {
		if validateCorrection(rule) == nil {
			t.Errorf("invalid rule %q -> %q accepted", rule.Heard, rule.Meant)
		}
	}
	if validateCorrection(correctionRule{Heard: "a", Meant: "\xff"}) == nil || validateCorrection(correctionRule{Heard: "\xc3", Meant: "b"}) == nil {
		t.Error("invalid UTF-8 accepted")
	}
}

func TestTheListIsTaughtForgottenAndReadBackThroughThePrivateStore(t *testing.T) {
	host, ctx := &correctionHost{files: map[string][]byte{}}, context.Background()
	call := func(op string, args aiiosdk.Object) (any, error) {
		if err := validateVocabulary(op, args); err != nil {
			return nil, err
		}
		return vocabularyCall(ctx, host, op, args)
	}
	value, err := call("vocabulary.list", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	if revision, rules, body := listed(t, value); revision != 0 || len(rules) != 0 || body["applies"] != "next_session" || len(host.files) != 0 {
		t.Fatalf("an absent list is revision 0 and empty, and reading creates nothing: %v %v", value, host.files)
	}

	value, err = call("vocabulary.correct", confirmed(map[string]any{"heard": "Kwin", "meant": "Quinn", "revision": 0}))
	if err != nil {
		t.Fatal(err)
	}
	if revision, rules, body := listed(t, value); revision != 1 || len(rules) != 1 || rules[0] != (correctionRule{"Kwin", "Quinn"}) || body["changed"] != true {
		t.Fatalf("first rule not taken: %v", value)
	}
	want := `{"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"Kwin","meant":"Quinn"}]}`
	if string(host.files[correctionsPath]) != want || len(host.files) != 1 {
		t.Fatalf("stored form differs or a stage was left: %q %d", host.files[correctionsPath], len(host.files))
	}
	for _, used := range host.calls {
		path := used[strings.Index(used, ":")+1:]
		stage := strings.HasPrefix(path, "uid/.corrections-") && strings.HasSuffix(path, ".pending") && len(path) == len("uid/.corrections-")+64+len(".pending")
		if path != correctionsPath && !stage {
			t.Fatalf("a path outside the list and its stage was used: %s", used)
		}
	}

	// A stale revision changes nothing and writes nothing.
	before := len(host.calls)
	if _, err = call("vocabulary.correct", confirmed(map[string]any{"heard": "rowen", "meant": "Rowan", "revision": 0})); err == nil || !strings.Contains(err.Error(), "revision 0 is not the list's revision 1") {
		t.Fatalf("stale revision accepted: %v", err)
	}
	if string(host.files[correctionsPath]) != want || len(host.calls) != before+1 {
		t.Fatal("a refused change wrote to the store")
	}

	// Teaching a phrase again, in any case, replaces what it meant.
	if value, err = call("vocabulary.correct", confirmed(map[string]any{"heard": "KWIN", "meant": "Kwen", "revision": 1})); err != nil {
		t.Fatal(err)
	}
	if revision, rules, _ := listed(t, value); revision != 2 || len(rules) != 1 || rules[0] != (correctionRule{"KWIN", "Kwen"}) {
		t.Fatalf("re-teaching did not replace: %v", value)
	}
	// The same rule again changes nothing, keeps the revision and publishes nothing.
	before = len(host.calls)
	if value, err = call("vocabulary.correct", confirmed(map[string]any{"heard": "KWIN", "meant": "Kwen", "revision": 2})); err != nil {
		t.Fatal(err)
	}
	if revision, _, body := listed(t, value); revision != 2 || body["changed"] != false || len(host.calls) != before+1 {
		t.Fatalf("an unchanged rule advanced the list: %v", value)
	}

	if value, err = call("vocabulary.forget", confirmed(map[string]any{"heard": "kwin", "revision": 2})); err != nil {
		t.Fatal(err)
	}
	if revision, rules, body := listed(t, value); revision != 3 || len(rules) != 0 || body["changed"] != true {
		t.Fatalf("forget did not remove: %v", value)
	}
	if value, err = call("vocabulary.forget", confirmed(map[string]any{"heard": "nobody", "revision": 3})); err != nil {
		t.Fatal(err)
	}
	if revision, _, body := listed(t, value); revision != 3 || body["changed"] != false {
		t.Fatalf("forgetting an unknown phrase changed the list: %v", value)
	}
	if string(host.files[correctionsPath]) != `{"schema":"aiii.voice.corrections","revision":3,"rules":[]}` {
		t.Fatalf("an emptied list is not stored as empty: %q", host.files[correctionsPath])
	}
}

func TestTheListIsBoundedAndRefusesWhatTheEngineWould(t *testing.T) {
	host, ctx := &correctionHost{files: map[string][]byte{}}, context.Background()
	doc := correctionDocument{Revision: 64}
	for i := 0; i < maxCorrections; i++ {
		doc.Rules = append(doc.Rules, correctionRule{Heard: "w" + strings.Repeat("x", i), Meant: "y"})
	}
	host.files[correctionsPath] = doc.encode()
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "one more", "meant": "x", "revision": 64})); err == nil || !strings.Contains(err.Error(), "full") {
		t.Fatalf("a 65th rule was accepted: %v", err)
	}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "W", "meant": "z", "revision": 64})); err != nil {
		t.Fatalf("a full list refused to re-teach a held phrase: %v", err)
	}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "a,b", "meant": "c", "revision": 65})); err == nil {
		t.Fatal("a heard phrase with punctuation was accepted")
	}
}

func TestAChangeIsTrustedOnlyAfterADurableExactPublication(t *testing.T) {
	ctx := context.Background()
	stored := correctionDocument{Revision: 1, Rules: []correctionRule{{"Kwin", "Quinn"}}}.encode()
	teach := confirmed(map[string]any{"heard": "rowen", "meant": "Rowan", "revision": 1})

	raced := correctionDocument{Revision: 2, Rules: []correctionRule{{"Kwin", "Kwen"}}}.encode()
	host := &correctionHost{files: map[string][]byte{correctionsPath: stored}, raceOnce: raced}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", teach); err == nil || !strings.Contains(err.Error(), "changed since it was read") {
		t.Fatalf("a publish over a list that changed was not refused: %v", err)
	}
	if string(host.files[correctionsPath]) != string(raced) {
		t.Fatal("the other writer's list was overwritten")
	}

	host = &correctionHost{files: map[string][]byte{correctionsPath: stored}, durability: "unknown"}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", teach); err == nil || !strings.Contains(err.Error(), "unresolved") {
		t.Fatalf("an unsynced publication was reported as saved: %v", err)
	}

	for _, op := range []string{"fs.read", "fs.write", "fs.publish"} {
		host = &correctionHost{files: map[string][]byte{correctionsPath: stored}, deny: op}
		if _, err := vocabularyCall(ctx, host, "vocabulary.correct", teach); err == nil {
			t.Fatalf("a refused %s was reported as saved", op)
		}
		if op != "fs.publish" && string(host.files[correctionsPath]) != string(stored) {
			t.Fatalf("a refused %s changed the list", op)
		}
	}

	// Refused storage is unavailable, never an empty list.
	host = &correctionHost{files: map[string][]byte{correctionsPath: stored}, deny: "fs.read"}
	if _, err := vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err == nil {
		t.Fatal("a store that refused the read answered an empty list")
	}
	for _, unreadable := range []string{`[]`, `{"schema":"other","revision":1,"rules":[]}`, `{"schema":"aiii.voice.corrections","revision":1,"rules":[],"extra":1}`,
		`{"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a,b","meant":"c"}]}`, `{"schema":"aiii.voice.corrections","revision":1,"rules":[{"heard":"a","meant":"b"},{"heard":"A","meant":"c"}]}`,
		`{"schema":"aiii.voice.corrections","revision":1}`, `{"schema":"aiii.voice.corrections","revision":1,"rules":null}`} {
		host = &correctionHost{files: map[string][]byte{correctionsPath: []byte(unreadable)}}
		if _, err := vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err == nil {
			t.Fatalf("an unreadable stored list was listed: %s", unreadable)
		}
		if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "x", "meant": "y", "revision": 1})); err == nil || string(host.files[correctionsPath]) != unreadable {
			t.Fatalf("an unreadable stored list was written over: %s", unreadable)
		}
	}
}

func TestChangesNeedTheOperatorsConfirmationAndOnlyTheirOwnArguments(t *testing.T) {
	if err := validateVocabulary("vocabulary.list", aiiosdk.Object(`{"_host_now_ms":1}`)); err != nil {
		t.Fatal("listing needs no confirmation", err)
	}
	if validateVocabulary("vocabulary.list", aiiosdk.Object(`{"heard":"x"}`)) == nil {
		t.Fatal("list took an argument")
	}
	if err := validateVocabulary("vocabulary.correct", confirmed(map[string]any{"heard": "Kwin", "meant": "Quinn", "revision": 0})); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(map[string]any{"heard": "Kwin", "meant": "Quinn", "revision": 0})
	if err := validateVocabulary("vocabulary.correct", aiiosdk.Object(raw)); err == nil {
		t.Fatal("a change without the host's time and confirmation was accepted")
	}
	stale := map[string]any{"heard": "Kwin", "meant": "Quinn", "revision": 0, "_host_now_ms": time.Date(2025, 1, 2, 3, 4, 5, 0, time.UTC).UnixMilli(),
		"_host_operator_act": map[string]any{"id": "a", "confirmed_at": "2025-01-02T02:00:00Z"}}
	raw, _ = json.Marshal(stale)
	if err := validateVocabulary("vocabulary.correct", aiiosdk.Object(raw)); err == nil {
		t.Fatal("an hour-old confirmation was accepted")
	}
	for _, bad := range []map[string]any{{"heard": "Kwin", "revision": 0}, {"heard": "Kwin", "meant": "Quinn"}, {"heard": "Kwin", "meant": "Quinn", "revision": 0, "path": "x"}} {
		if validateVocabulary("vocabulary.correct", confirmed(bad)) == nil {
			t.Fatalf("accepted %v", bad)
		}
	}
	if validateVocabulary("vocabulary.forget", confirmed(map[string]any{"heard": "Kwin", "meant": "Quinn", "revision": 0})) == nil {
		t.Fatal("forget took meant")
	}
	if err := validateVocabulary("speaker.list", aiiosdk.Object(`not json`)); err != nil {
		t.Fatal("another family's call was judged here", err)
	}
}

func TestASessionIsHandedTheStoredListOrNothing(t *testing.T) {
	ctx := context.Background()
	stored := correctionDocument{Revision: 4, Rules: []correctionRule{{"Kwin", "Quinn"}}}.encode()
	if got := sessionCorrections(ctx, &correctionHost{files: map[string][]byte{}}); got != nil {
		t.Fatalf("no stored list handed a session %s", got)
	}
	if got := sessionCorrections(ctx, &correctionHost{files: map[string][]byte{correctionsPath: stored}}); string(got) != string(stored) {
		t.Fatalf("the stored list did not reach the session exactly: %s", got)
	}
	if got := sessionCorrections(ctx, &correctionHost{files: map[string][]byte{correctionsPath: stored}, deny: "fs.read"}); got != nil {
		t.Fatal("a refused read handed the session something")
	}
	if got := sessionCorrections(ctx, &correctionHost{files: map[string][]byte{correctionsPath: []byte("{not json")}}); got != nil {
		t.Fatal("bytes that are not JSON were handed to the worker's control line")
	}
	// Bytes that are JSON but not a list this engine holds do reach the worker:
	// it refuses them whole and says so in the session's readback.
	odd := []byte(`{"schema":"other"}`)
	if got := sessionCorrections(ctx, &correctionHost{files: map[string][]byte{correctionsPath: odd}}); string(got) != string(odd) {
		t.Fatal("the worker was not given the stored bytes to judge")
	}

	reply := settingsReply{settingsQuery: settingsQuery{ID: 7, SessionID: "s"}, Values: json.RawMessage(`{}`), Corrections: stored}
	line, _ := json.Marshal(privateRequest{Settings: &reply})
	if string(line) != `{"settings_reply":{"id":7,"session_id":"s","values":{},"corrections":`+string(stored)+`}}` {
		t.Fatalf("settings reply shape differs: %s", line)
	}
	reply.Corrections = nil
	line, _ = json.Marshal(privateRequest{Settings: &reply})
	if strings.Contains(string(line), "corrections") {
		t.Fatalf("a session with no stored list was sent a corrections member: %s", line)
	}
}

func TestVocabularyOperationsAreDeclaredWithShippedSchemas(t *testing.T) {
	found := 0
	for _, descriptor := range declaredPlugin().Descriptors() {
		if !vocabularyOperation(descriptor.ID) {
			continue
		}
		found++
		if descriptor.Family != "vocabulary" || descriptor.OperatorConfirms != (descriptor.ID != "vocabulary.list") || len(descriptor.Capabilities) != 1 || descriptor.Capabilities[0] != "fs.private" {
			t.Fatalf("wrong family, confirmation or capability: %+v", descriptor)
		}
		for _, path := range []string{descriptor.Input, descriptor.Output} {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("%s: %v", descriptor.ID, err)
			}
			if err = aiiospkg.CheckSchemaSubset(raw); err != nil {
				t.Errorf("%s %s: %v", descriptor.ID, path, err)
			}
		}
		for _, example := range descriptor.Examples {
			var args map[string]any
			if err := json.Unmarshal([]byte(example), &args); err != nil {
				t.Fatal(err)
			}
			object := aiiosdk.Object(example)
			if descriptor.OperatorConfirms {
				object = confirmed(args)
			}
			if err := validateVocabulary(descriptor.ID, object); err != nil {
				t.Fatalf("published example of %s invalid: %v", descriptor.ID, err)
			}
		}
	}
	if found != 3 {
		t.Fatalf("%d vocabulary operations declared", found)
	}
	q := correctionsQuery()
	if !q.valid() || q.target() != correctionsPath || q.limit() != snapshotPageBytes {
		t.Fatal("the correction list is not a fixed, bounded private resource")
	}
	q.Action, q.Upload, q.Data = "stage", strings.Repeat("ab", 32), "e30="
	if !q.valid() || q.target() != "uid/.corrections-"+strings.Repeat("ab", 32)+".pending" {
		t.Fatalf("stage path differs: %s", q.target())
	}
	size, old := int64(10), "2020-01-01T00:00:00Z"
	row := recordingEntry{Name: ".corrections-" + strings.Repeat("ab", 32) + ".pending", Size: &size, Modified: old}
	if !expiredVoiceStage("uid", row, time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC)) {
		t.Fatal("an abandoned correction stage is not prunable")
	}
}
