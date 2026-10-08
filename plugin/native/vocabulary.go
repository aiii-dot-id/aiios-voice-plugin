package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// THE CORRECTION LIST (docs/RECOGNIZER_CORRECTIONS.md). What the recognizer
// writes for a name it does not know, and what was meant. The carrier owns
// the list's storage and the three operations that read and change it; the
// worker only applies the list a session is handed with its settings.
//
// Recognized speech is recorded as the speaker's own words, and a rule changes
// that record. So a rule is added or forgotten only by an act the operator
// confirmed, against the exact revision that was read, and the engine sends
// the recognizer's own words beside every transcript a rule changed.
const (
	correctionsPath     = "uid/corrections.json"
	correctionsResource = "corrections"
	correctionsSchema   = "aiii.voice.corrections"
	maxCorrections      = 64
	maxCorrectionBytes  = 64
	maxCorrectionWords  = 4
)

type correctionRule struct {
	Heard string `json:"heard"`
	Meant string `json:"meant"`
}

type correctionDocument struct {
	Schema   string           `json:"schema"`
	Revision uint64           `json:"revision"`
	Rules    []correctionRule `json:"rules"`
}

func vocabularyOperation(op string) bool {
	return op == "vocabulary.list" || op == "vocabulary.correct" || op == "vocabulary.forget"
}

// wordByte is the engine's own rule for what a heard word is made of
// (runtime/native/session/corrections.h): letters, digits, apostrophes and
// every byte of a non-ASCII character.
func wordByte(c byte) bool {
	return c >= 0x80 || (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '\''
}

// foldHeard is how two heard phrases are compared: without ASCII case, the
// typographic apostrophe the same letter as the plain one.
func foldHeard(s string) string {
	s = strings.ReplaceAll(s, "’", "'")
	b := []byte(s)
	for i, c := range b {
		if c >= 'A' && c <= 'Z' {
			b[i] = c - 'A' + 'a'
		}
	}
	return string(b)
}

// validateCorrection refuses what the worker would refuse, in the same words
// where a caller will read them. spec/correction_vectors.json holds both to it.
func validateCorrection(rule correctionRule) error {
	h, m := rule.Heard, rule.Meant
	if len(h) == 0 || len(h) > maxCorrectionBytes {
		return errors.New("heard must be 1..64 bytes")
	}
	if !utf8.ValidString(h) || !utf8.ValidString(m) {
		return errors.New("text must be valid UTF-8")
	}
	words := strings.Split(h, " ")
	for _, word := range words {
		if word == "" {
			return errors.New("heard is words separated by single spaces")
		}
		for i := 0; i < len(word); i++ {
			if !wordByte(word[i]) {
				return errors.New("heard is words of letters, digits and apostrophes")
			}
		}
		if strings.Trim(strings.ReplaceAll(word, "’", "'"), "'") == "" {
			return errors.New("heard needs a letter or digit in every word")
		}
	}
	if len(words) > maxCorrectionWords {
		return errors.New("heard is at most four words")
	}
	if len(m) == 0 || len(m) > maxCorrectionBytes {
		return errors.New("meant must be 1..64 bytes")
	}
	if m[0] == ' ' || m[len(m)-1] == ' ' {
		return errors.New("meant must not begin or end with a space")
	}
	for i := 0; i < len(m); i++ {
		if m[i] < 0x20 || m[i] == 0x7f {
			return errors.New("meant must be one line of text")
		}
	}
	if m == h {
		return errors.New("heard and meant are the same")
	}
	return nil
}

// parseCorrections reads the stored form whole or refuses it whole.
func parseCorrections(raw []byte) (correctionDocument, error) {
	var doc correctionDocument
	var fields map[string]json.RawMessage
	bad := errors.New("stored correction list is unreadable")
	if json.Unmarshal(raw, &fields) != nil || len(fields) != 3 {
		return doc, bad
	}
	for _, name := range []string{"schema", "revision", "rules"} {
		if _, ok := fields[name]; !ok {
			return doc, bad
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&doc) != nil || doc.Schema != correctionsSchema || doc.Rules == nil || len(doc.Rules) > maxCorrections || doc.Revision > 1<<53-1 {
		return doc, bad
	}
	seen := map[string]bool{}
	for _, rule := range doc.Rules {
		key := foldHeard(rule.Heard)
		if validateCorrection(rule) != nil || seen[key] {
			return doc, bad
		}
		seen[key] = true
	}
	return doc, nil
}

func (d correctionDocument) encode() []byte {
	if d.Rules == nil {
		d.Rules = []correctionRule{}
	}
	d.Schema = correctionsSchema
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(d) // strings, a number and a slice of two-string structs cannot fail
	return bytes.TrimRight(out.Bytes(), "\n")
}

func (d correctionDocument) result(changed *bool) map[string]any {
	rules := d.Rules
	if rules == nil {
		rules = []correctionRule{}
	}
	body := map[string]any{
		"revision": d.Revision, "rules": rules,
		"limits":  map[string]any{"rules": maxCorrections, "bytes": maxCorrectionBytes, "heard_words": maxCorrectionWords},
		"applies": "next_session",
	}
	if changed != nil {
		body["changed"] = *changed
	}
	return map[string]any{"status": "succeeded", "operation_result": body}
}

func validateVocabulary(op string, args aiiosdk.Object) error {
	if !vocabularyOperation(op) {
		return nil
	}
	var fields map[string]json.RawMessage
	if len(args) > 4096 || json.Unmarshal(args, &fields) != nil || fields == nil {
		return errors.New("bounded vocabulary arguments required")
	}
	allowed := map[string]bool{}
	if op != "vocabulary.list" {
		allowed["heard"], allowed["revision"] = true, true
	}
	if op == "vocabulary.correct" {
		allowed["meant"] = true
	}
	for name := range fields {
		if strings.HasPrefix(name, "_host") {
			continue
		}
		if !allowed[name] {
			return errors.New("unknown vocabulary argument")
		}
	}
	if op == "vocabulary.list" {
		return nil
	}
	for name := range allowed {
		if _, ok := fields[name]; !ok {
			return errors.New(name + " required; call vocabulary.list for the current revision")
		}
	}
	var nowMillis int64
	if json.Unmarshal(fields["_host_now_ms"], &nowMillis) != nil || nowMillis <= 0 {
		return errors.New("host invocation time required")
	}
	stamp, ok := aiiosdk.OperatorAct(args)
	if !ok || len(stamp.ID) > 128 {
		return errors.New("host operator confirmation required")
	}
	when, err := time.Parse(time.RFC3339Nano, stamp.ConfirmedAt)
	now := time.UnixMilli(nowMillis)
	if err != nil || when.Before(now.Add(-10*time.Minute)) || when.After(now.Add(time.Minute)) {
		return errors.New("host operator confirmation invalid or expired")
	}
	return nil
}

func correctionsQuery() snapshotQuery {
	// The correlation fields are the worker bridge's; here the carrier is the
	// only caller and they only have to be well formed.
	return snapshotQuery{settingsQuery: settingsQuery{ID: 1, SessionID: "vocabulary"}, Resource: correctionsResource}
}

func correctionsCall(ctx context.Context, session privateStorageCaller, q snapshotQuery) (json.RawMessage, error) {
	op, args := q.call()
	raw, err := session.HostCallTo(ctx, op, map[string]any{"root": "private", "path": q.target()}, args)
	if err != nil {
		return nil, errors.New("private store unavailable")
	}
	return snapshotValue(raw, q)
}

// storedCorrections returns the stored list's exact bytes. Only a typed
// not-found means there is no list yet; every other failure is unavailable,
// never empty.
func storedCorrections(ctx context.Context, session privateStorageCaller) (stored []byte, absent bool, err error) {
	q := correctionsQuery()
	q.Digest = true
	value, err := correctionsCall(ctx, session, q)
	var typed snapshotFailure
	if errors.As(err, &typed) && typed.reason == "FS_NOT_FOUND" {
		return nil, true, nil
	}
	if err != nil {
		return nil, false, errors.New("correction list unavailable; nothing changed")
	}
	var page struct {
		Data string `json:"data_b64"`
		Size uint64 `json:"size"`
		EOF  bool   `json:"eof"`
		SHA  string `json:"sha256"`
	}
	if json.Unmarshal(value, &page) != nil {
		return nil, false, errors.New("correction list unavailable; nothing changed")
	}
	stored, derr := base64.StdEncoding.Strict().DecodeString(page.Data)
	digest := sha256.Sum256(stored)
	// The whole list is one page by its bound; a longer file is not this list.
	if derr != nil || uint64(len(stored)) != page.Size || !page.EOF || hex.EncodeToString(digest[:]) != page.SHA {
		return nil, false, errors.New("correction list read incomplete; nothing changed")
	}
	return stored, false, nil
}

func readCorrections(ctx context.Context, session privateStorageCaller) (doc correctionDocument, stored []byte, absent bool, err error) {
	stored, absent, err = storedCorrections(ctx, session)
	if err != nil {
		return doc, nil, false, err
	}
	if absent {
		return correctionDocument{Schema: correctionsSchema, Rules: []correctionRule{}}, nil, true, nil
	}
	doc, err = parseCorrections(stored)
	return doc, stored, false, err
}

// publishCorrections replaces exactly the bytes that were read (or creates
// the list where none was), and trusts the result only after the host said
// it is durable and the same bytes read back.
func publishCorrections(ctx context.Context, session privateStorageCaller, next correctionDocument, base []byte, absent bool) error {
	candidate := next.encode()
	if len(candidate) > snapshotPageBytes {
		return errors.New("correction list exceeds its stored bound")
	}
	var token [32]byte
	if _, err := rand.Read(token[:]); err != nil {
		return errors.New("upload identity unavailable; nothing changed")
	}
	stage := correctionsQuery()
	stage.Action, stage.Upload, stage.Data = "stage", hex.EncodeToString(token[:]), base64.StdEncoding.EncodeToString(candidate)
	if _, err := correctionsCall(ctx, session, stage); err != nil {
		return errors.New("correction list could not be staged; nothing changed")
	}
	sum := sha256.Sum256(candidate)
	publish := correctionsQuery()
	publish.Action, publish.Upload, publish.SHA = "publish", stage.Upload, hex.EncodeToString(sum[:])
	if absent {
		publish.Absent = true
	} else {
		was := sha256.Sum256(base)
		publish.Expected = hex.EncodeToString(was[:])
	}
	value, err := correctionsCall(ctx, session, publish)
	var typed snapshotFailure
	if errors.As(err, &typed) {
		return errors.New("the correction list changed since it was read; not saved: call vocabulary.list and ask for a fresh confirmation")
	}
	unresolved := errors.New("correction list publication unresolved; do not assume it is unchanged, and do not retry automatically: call vocabulary.list")
	if err != nil {
		return unresolved
	}
	var receipt struct {
		Size       uint64 `json:"size"`
		Durable    bool   `json:"durable"`
		Durability string `json:"durability"`
	}
	if json.Unmarshal(value, &receipt) != nil || receipt.Size != uint64(len(candidate)) || !receipt.Durable || (receipt.Durability != "synced" && receipt.Durability != "file-synced") {
		return unresolved
	}
	_, stored, gone, err := readCorrections(ctx, session)
	if err != nil || gone || !bytes.Equal(stored, candidate) {
		return unresolved
	}
	return nil
}

func (c *carrier) vocabularyStore(control *aiiosdk.Control) {
	c.mu.Lock()
	select {
	case <-c.stop:
		c.mu.Unlock()
		control.Answer(nil, errors.New("worker lane ended"))
		return
	default:
	}
	c.session = control.Session
	c.workers.Add(1)
	c.mu.Unlock()
	go func() {
		defer c.workers.Done()
		ctx, cancel := context.WithTimeout(c.ctx, 10*time.Second)
		defer cancel()
		// One change at a time: two confirmed acts never read the same base.
		c.vocabulary.Lock()
		defer c.vocabulary.Unlock()
		result, err := vocabularyCall(ctx, control.Session, control.Op, control.Args)
		control.Answer(result, err)
	}()
}

func vocabularyCall(ctx context.Context, session privateStorageCaller, op string, args aiiosdk.Object) (any, error) {
	doc, stored, absent, err := readCorrections(ctx, session)
	if err != nil {
		return nil, err
	}
	if op == "vocabulary.list" {
		return doc.result(nil), nil
	}
	var request struct {
		Heard    string  `json:"heard"`
		Meant    string  `json:"meant"`
		Revision *uint64 `json:"revision"`
	}
	if json.Unmarshal(args, &request) != nil || request.Revision == nil {
		return nil, errors.New("heard and a whole-number revision required; call vocabulary.list")
	}
	if *request.Revision != doc.Revision {
		return nil, fmt.Errorf("revision %d is not the list's revision %d; call vocabulary.list and ask for a fresh confirmation", *request.Revision, doc.Revision)
	}
	key, at := foldHeard(request.Heard), -1
	for i, rule := range doc.Rules {
		if foldHeard(rule.Heard) == key {
			at = i
		}
	}
	changed := false
	switch op {
	case "vocabulary.correct":
		rule := correctionRule{Heard: request.Heard, Meant: request.Meant}
		if err := validateCorrection(rule); err != nil {
			return nil, err
		}
		if at >= 0 {
			changed = doc.Rules[at] != rule
			doc.Rules[at] = rule
		} else {
			if len(doc.Rules) == maxCorrections {
				return nil, errors.New("the correction list is full (64 rules); forget one first")
			}
			doc.Rules, changed = append(doc.Rules, rule), true
		}
	case "vocabulary.forget":
		if request.Heard == "" || len(request.Heard) > maxCorrectionBytes {
			return nil, errors.New("heard must be 1..64 bytes")
		}
		if at >= 0 {
			doc.Rules, changed = append(doc.Rules[:at:at], doc.Rules[at+1:]...), true
		}
	default:
		return nil, errors.New("unknown vocabulary operation")
	}
	if !changed {
		return doc.result(&changed), nil
	}
	doc.Revision++
	if err := publishCorrections(ctx, session, doc, stored, absent); err != nil {
		return nil, err
	}
	return doc.result(&changed), nil
}

// sessionCorrections is what a session is handed beside its settings: the
// stored list's exact bytes, or nothing. A list that is absent, or that the
// store did not yield in time, hands the session nothing and never fails or
// delays its open beyond the settings bound; the second case is said once on
// the lifecycle log. What the bytes hold is the worker's to judge and report.
func sessionCorrections(ctx context.Context, session privateStorageCaller) json.RawMessage {
	stored, absent, err := storedCorrections(ctx, session)
	if absent {
		return nil
	}
	if err != nil || !json.Valid(stored) {
		fmt.Fprintln(os.Stderr, `AII_VOICE_CORRECTIONS {"component":"voice-carrier","event":"corrections_unavailable"}`)
		return nil
	}
	return json.RawMessage(stored)
}
