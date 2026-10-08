package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode"

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

// correctionVectors is spec/correction_vectors.json, the file the worker's
// validator is held to as well (runtime/native/session/corrections_test.cpp).
type correctionVectors struct {
	Valid   []correctionRule `json:"valid"`
	Invalid []correctionRule `json:"invalid"`
	Refused []struct {
		First    string `json:"first"`
		Last     string `json:"last"`
		Category string `json:"category"`
	} `json:"refused_code_points"`
	Malformed []struct {
		Hex string `json:"utf8_hex"`
	} `json:"malformed_utf8_hex"`
	Joiners []string         `json:"joiners"`
	Spaces  []codePointRange `json:"spaces"`
}

func readCorrectionVectors(t *testing.T) correctionVectors {
	t.Helper()
	raw, err := os.ReadFile("../../spec/correction_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors correctionVectors
	if json.Unmarshal(raw, &vectors) != nil || len(vectors.Valid) < 33 || len(vectors.Invalid) < 67 || len(vectors.Malformed) < 14 {
		t.Fatal("vector file incomplete")
	}
	return vectors
}

// refused is the vectors' table of what neither side of a rule may hold: the
// general category of each such code point, and nothing for any other.
func (v correctionVectors) refused(t *testing.T) map[rune]string {
	t.Helper()
	table := map[rune]string{}
	for _, row := range v.Refused {
		first, ferr := strconv.ParseUint(row.First, 16, 32)
		last, lerr := strconv.ParseUint(row.Last, 16, 32)
		if ferr != nil || lerr != nil || first > last || last > unicode.MaxRune {
			t.Fatalf("refused range %q..%q is unreadable", row.First, row.Last)
		}
		for r := rune(first); r <= rune(last); r++ {
			table[r] = row.Category
		}
	}
	if len(table) != 237 {
		t.Fatalf("the vectors refuse %d code points, not the 237 of Cc, Cf, Zl and Zp", len(table))
	}
	return table
}

// joined is the vectors' two other tables: the joiners that what was meant
// takes inside a word, and the spaces a joiner may not stand beside.
func (v correctionVectors) joined(t *testing.T) (joiners, spaces map[rune]bool) {
	t.Helper()
	joiners, spaces = map[rune]bool{}, codePoints(t, v.Spaces)
	for _, text := range v.Joiners {
		joiners[rune(mustHex(t, text))] = true
	}
	if len(joiners) != 2 || !joiners[0x200C] || !joiners[0x200D] || len(spaces) != 17 {
		t.Fatalf("the vectors hold %d joiners and %d spaces", len(joiners), len(spaces))
	}
	return joiners, spaces
}

func TestCorrectionRulesFollowTheSharedVectors(t *testing.T) {
	vectors := readCorrectionVectors(t)
	for _, rule := range vectors.Valid {
		if err := validateCorrection(rule); err != nil {
			t.Errorf("valid rule %q -> %q refused: %v", rule.Heard, rule.Meant, err)
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
	// Bytes that are not UTF-8 are refused wherever they stand, not read loosely.
	for _, row := range vectors.Malformed {
		raw, err := hex.DecodeString(row.Hex)
		if err != nil || len(raw) == 0 {
			t.Fatalf("malformed vector %q is unreadable", row.Hex)
		}
		for _, text := range []string{string(raw), "b" + string(raw), "b" + string(raw) + "c"} {
			if validateCorrection(correctionRule{Heard: "a", Meant: text}) == nil || validateCorrection(correctionRule{Heard: text, Meant: "c"}) == nil {
				t.Errorf("malformed UTF-8 %s accepted as %q", row.Hex, text)
			}
		}
	}
}

// A rule is confirmed by an operator reading it, so neither side holds a
// character that cannot be read where it stands. Every code point there is,
// on each side, is refused exactly where the vectors' table says; the worker's
// own list (runtime/native/session/corrections.h) is held to the same table.
// The one exception is asked about everywhere it could stand: in what was
// meant a joiner is spelling inside a word, between two characters that are
// neither a space nor a joiner, and nowhere else.
func TestNeitherSideOfARuleHoldsWhatAnOperatorCannotRead(t *testing.T) {
	vectors := readCorrectionVectors(t)
	refused := vectors.refused(t)
	joiners, spaces := vectors.joined(t)
	categories := []struct {
		name  string
		table *unicode.RangeTable
	}{{"Cc", unicode.Cc}, {"Cf", unicode.Cf}, {"Zl", unicode.Zl}, {"Zp", unicode.Zp}}
	for r := rune(0); r <= unicode.MaxRune; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue // surrogates are not characters: no text holds one
		}
		category := ""
		for _, c := range categories {
			if unicode.Is(c.table, r) {
				category = c.name
			}
		}
		if category != refused[r] {
			t.Fatalf("U+%04X is %q in this toolchain's Unicode %s and %q in the vectors: the worker's list, the vectors and the carrier move together or a list one wrote is refused by the other", r, category, unicode.Version, refused[r])
		}
		if spaces[r] != unicode.Is(unicode.Zs, r) {
			t.Fatalf("U+%04X: this toolchain's Unicode %s and the vectors differ on what a space is", r, unicode.Version)
		}
		out := refused[r] != ""
		// What a heard phrase was made of before this table: a space joins two words.
		word := r >= 0x80 || r == ' ' || r == '\'' || (r >= '0' && r <= '9') || (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z')
		meant := validateCorrection(correctionRule{Heard: "a", Meant: "b" + string(r) + "c"}) != nil
		heard := validateCorrection(correctionRule{Heard: "a" + string(r) + "b", Meant: "c"}) != nil
		beside := validateCorrection(correctionRule{Heard: "a", Meant: string(r) + string(rune(0x200D)) + string(r)}) != nil
		if meant != (out && !joiners[r]) || heard != (out || !word) || beside != (out || spaces[r]) {
			t.Fatalf("U+%04X: refused in meant %v, in heard %v, on both sides of a joiner %v; the vectors refuse it %v", r, meant, heard, beside, out)
		}
	}
	const unjoined = "meant holds a joiner outside a word; a joiner is taken only inside a word"
	said := func(meant, want, why string) {
		t.Helper()
		err := validateCorrection(correctionRule{Heard: "a", Meant: meant})
		if (want == "") != (err == nil) || (err != nil && err.Error() != want) {
			t.Errorf("%s (%+q): %v", why, meant, err)
		}
	}
	for joiner := range joiners {
		j := string(joiner)
		said(j+"bc", unjoined, "a joiner first")
		said("bc"+j, unjoined, "a joiner last")
		said(j, unjoined, "a joiner alone")
		for space := range spaces {
			said("b"+string(space)+j+"c", unjoined, "a joiner after a space")
			said("b"+j+string(space)+"c", unjoined, "a joiner before a space")
			said("b"+string(space)+"c"+j+"d", "", "a space, then a joined word")
		}
		for other := range joiners {
			said("b"+j+string(other)+"c", unjoined, "two joiners together")
			said("b"+j+"c"+string(other)+"d", "", "two joiners, a letter between them")
		}
		if err := validateCorrection(correctionRule{Heard: "a" + j + "b", Meant: "c"}); err == nil || err.Error() != "heard must not hold format characters, such as zero-width and direction marks" {
			t.Errorf("U+%04X in what was heard: %v", joiner, err)
		}
	}
	// The refusal names the side it is about, and says more than "not a letter"
	// about a character the caller cannot see in what it sent.
	for side, rule := range map[string]correctionRule{"heard": {"Kw\xe2\x80\x8bin", "Quinn"}, "meant": {"Kwin", "Quinn\xe2\x80\xae"}} {
		if err := validateCorrection(rule); err == nil || err.Error() != side+" must not hold format characters, such as zero-width and direction marks" {
			t.Errorf("a format character in %s: %v", side, err)
		}
	}
	for side, rule := range map[string]correctionRule{"heard is words of letters, digits and apostrophes": {"a\xe2\x80\xa8b", "c"}, "meant must be one line of text": {"a", "b\xe2\x80\xa8c"}} {
		if err := validateCorrection(rule); err == nil || err.Error() != side {
			t.Errorf("a line separator: %v, not %q", err, side)
		}
	}
}

// The host holds a call to its input schema before the operator is asked, and
// the carrier sees a change only after it was confirmed. So the schemas are
// what keeps a confirmation from showing what a rule may not hold, and they
// refuse the same code points; they never refuse a rule the list would hold.
// What was meant is held to the pattern a speaker's label has, which takes a
// joiner inside a word; what was heard to one that takes none.
func TestAConfirmationIsNeverShownWhatARuleMayNotHold(t *testing.T) {
	vectors := readCorrectionVectors(t)
	refused := vectors.refused(t)
	joiners, spaces := vectors.joined(t)
	label := labelPatterns(t)["schemas/speaker-enroll.input.json label"].String()
	for _, shown := range []struct {
		file, name string
		joined     bool
	}{
		{"schemas/vocabulary-correct.input.json", "heard", false}, {"schemas/vocabulary-correct.input.json", "meant", true}, {"schemas/vocabulary-forget.input.json", "heard", false},
	} {
		var document struct {
			Properties map[string]struct {
				Type                 string
				MinLength, MaxLength int
				Pattern              string
			}
		}
		raw, err := os.ReadFile(shown.file)
		if err != nil || json.Unmarshal(raw, &document) != nil {
			t.Fatalf("%s is unreadable: %v", shown.file, err)
		}
		text := document.Properties[shown.name]
		if text.Type != "string" || text.MinLength != 1 || text.MaxLength != maxCorrectionChars || text.Pattern == "" {
			t.Fatalf("%s %s is not bounded text with a pattern: %+v", shown.file, shown.name, text)
		}
		pattern, err := regexp.Compile(text.Pattern)
		if err != nil {
			t.Fatal(err)
		}
		if (text.Pattern == label) != shown.joined {
			t.Errorf("%s %s: only what was meant is held to the pattern of a speaker's label", shown.file, shown.name)
		}
		for r := range refused {
			for at, held := range []string{string(r), string(r) + "b", "b" + string(r), "b" + string(r) + string(r) + "c", "b " + string(r) + "c", "b" + string(r) + " c", "b" + string(r) + "c"} {
				// Only a joiner, only in what was meant, only between two letters.
				if want := shown.joined && joiners[r] && at == 6; pattern.MatchString(held) != want {
					t.Errorf("%s %s: U+%04X in %+q: shown to the operator %v", shown.file, shown.name, r, held, !want)
				}
			}
		}
		for joiner := range joiners {
			for space := range spaces {
				for _, held := range []string{"b" + string(space) + string(joiner) + "c", "b" + string(joiner) + string(space) + "c"} {
					if pattern.MatchString(held) {
						t.Errorf("%s %s lets a joiner beside U+%04X reach the operator in %+q", shown.file, shown.name, space, held)
					}
				}
			}
			for other := range joiners {
				if pattern.MatchString("b" + string(joiner) + string(other) + "c") {
					t.Errorf("%s %s lets two joiners together reach the operator", shown.file, shown.name)
				}
			}
		}
		// The code points on either side of each refused range are text.
		for _, row := range vectors.Refused {
			first, _ := strconv.ParseUint(row.First, 16, 32)
			last, _ := strconv.ParseUint(row.Last, 16, 32)
			for _, r := range []rune{rune(first) - 1, rune(last) + 1} {
				if r < 0 || refused[r] != "" {
					continue
				}
				if !pattern.MatchString("b" + string(r) + "c") {
					t.Errorf("%s %s refuses U+%04X, which a rule may hold", shown.file, shown.name, r)
				}
			}
		}
		for _, rule := range vectors.Valid {
			side := rule.Heard
			if shown.name == "meant" {
				side = rule.Meant
			}
			if !pattern.MatchString(side) {
				t.Errorf("%s %s refuses the valid rule %q -> %q", shown.file, shown.name, rule.Heard, rule.Meant)
			}
		}
	}
}

// What cannot be read as written is not taught, and a stored list holding it
// is unreadable whole, as the worker finds it: nothing is written either way.
func TestARuleThatCannotBeReadAsWrittenIsNeitherTaughtNorListed(t *testing.T) {
	ctx := context.Background()
	for _, rule := range []correctionRule{{"Kwin", "\xe2\x80\xaenniuQ"}, {"Kwin", "Quinn\xe2\x80\xa8"}, {"Kw\xe2\x80\x8bin", "Quinn"}, {"Kwin\xc2\x85", "Quinn"}} {
		host := &correctionHost{files: map[string][]byte{}}
		if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": rule.Heard, "meant": rule.Meant, "revision": 0})); err == nil {
			t.Fatalf("%q -> %q was taught", rule.Heard, rule.Meant)
		}
		if len(host.files) != 0 || len(host.calls) != 1 {
			t.Fatalf("a refused rule reached the store: %v", host.calls)
		}
		stored := correctionDocument{Revision: 1, Rules: []correctionRule{{"rowen", "Rowan"}, rule}}.encode()
		host = &correctionHost{files: map[string][]byte{correctionsPath: stored}}
		if _, err := vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err == nil {
			t.Fatalf("a stored list holding %q -> %q was listed", rule.Heard, rule.Meant)
		}
	}

	// Bytes that are not UTF-8 are refused as sent. Decoding would have mended
	// them into U+FFFD: a rule nobody wrote, and one the worker never sees.
	sent := bytes.Replace(confirmed(map[string]any{"heard": "Kwin", "meant": "Quinn?", "revision": 0}), []byte("?"), []byte("\xff"), 1)
	host := &correctionHost{files: map[string][]byte{}}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", aiiosdk.Object(sent)); err == nil || err.Error() != "text must be valid UTF-8" || len(host.files) != 0 {
		t.Fatalf("a change holding a byte that is not UTF-8 was taken: %v %v", err, host.files)
	}
	stored := bytes.Replace(correctionDocument{Revision: 1, Rules: []correctionRule{{"Kwin", "Quinn?"}}}.encode(), []byte("?"), []byte("\xff"), 1)
	host = &correctionHost{files: map[string][]byte{correctionsPath: stored}}
	if _, err := vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err == nil {
		t.Fatal("a stored list holding a byte that is not UTF-8 was listed with the byte mended")
	}
	if _, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "x", "meant": "y", "revision": 1})); err == nil || !bytes.Equal(host.files[correctionsPath], stored) {
		t.Fatal("a stored list holding a byte that is not UTF-8 was written over")
	}
}

// A side of a rule is counted in characters, as the operations' schemas count
// it (maxLength), so what an operator is asked to confirm is what the list
// holds. Four bytes a character bound its bytes: a text beyond that is refused
// for its length without being read.
func TestASideIsCountedInCharactersAsTheSchemasCountIt(t *testing.T) {
	// A CJK character of three bytes and an emoji of four.
	cjk, emoji := "\xe6\x9d\xb1", "\xf0\x9f\x98\x80"
	for _, rule := range []correctionRule{
		{"a", strings.Repeat(cjk, 22)}, {"a", strings.Repeat(cjk, 64)}, {"a", strings.Repeat(emoji, 64)},
		{strings.Repeat(emoji, 64), "b"}, {strings.Repeat("a", 64), strings.Repeat("b", 64)},
	} {
		if err := validateCorrection(rule); err != nil {
			t.Errorf("a heard of %d characters and a meant of %d refused: %v", len([]rune(rule.Heard)), len([]rune(rule.Meant)), err)
		}
	}
	for _, refused := range []struct {
		rule correctionRule
		want string
	}{
		{correctionRule{"a", strings.Repeat(cjk, 65)}, "meant must be 1..64 characters"},
		{correctionRule{strings.Repeat(cjk, 65), "b"}, "heard must be 1..64 characters"},
		{correctionRule{"a", strings.Repeat(emoji, 65)}, "meant must be 1..64 characters"},
		{correctionRule{strings.Repeat("a", 65), "b"}, "heard must be 1..64 characters"},
		{correctionRule{"a", strings.Repeat("\xff", 257)}, "meant must be 1..64 characters"},
		{correctionRule{strings.Repeat("\xff", 257), "b"}, "heard must be 1..64 characters"},
		{correctionRule{"a", strings.Repeat("\xff", 256)}, "text must be valid UTF-8"},
		{correctionRule{strings.Repeat("\xff", 256), "b"}, "text must be valid UTF-8"},
	} {
		if err := validateCorrection(refused.rule); err == nil || err.Error() != refused.want {
			t.Errorf("a heard of %d bytes and a meant of %d: %v, not %q", len(refused.rule.Heard), len(refused.rule.Meant), err, refused.want)
		}
	}

	// Every schema that bounds a side counts what the carrier counts, and the
	// result states its limits in that unit.
	schema := func(file string) map[string]any {
		t.Helper()
		var document map[string]any
		raw, err := os.ReadFile(file)
		if err != nil || json.Unmarshal(raw, &document) != nil {
			t.Fatalf("%s is unreadable: %v", file, err)
		}
		return document
	}
	at := func(node any, path ...string) any {
		for _, name := range path {
			object, _ := node.(map[string]any)
			node = object[name]
		}
		return node
	}
	for _, side := range [][]string{
		{"schemas/vocabulary-correct.input.json", "properties", "heard"},
		{"schemas/vocabulary-correct.input.json", "properties", "meant"},
		{"schemas/vocabulary-forget.input.json", "properties", "heard"},
		{"schemas/vocabulary.output.json", "properties", "rules", "items", "properties", "heard"},
		{"schemas/vocabulary.output.json", "properties", "rules", "items", "properties", "meant"},
	} {
		if got := at(schema(side[0]), append(side[1:], "maxLength")...); got != float64(maxCorrectionChars) {
			t.Errorf("%v counts %v characters, the carrier %d", side, got, maxCorrectionChars)
		}
	}
	limits, _ := at(schema("schemas/vocabulary.output.json"), "properties", "limits").(map[string]any)
	stated, _ := limits["properties"].(map[string]any)
	if len(stated) != 3 || at(stated, "rules", "const") != float64(maxCorrections) || at(stated, "characters", "const") != float64(maxCorrectionChars) || at(stated, "heard_words", "const") != float64(maxCorrectionWords) {
		t.Errorf("the output schema's limits are not the carrier's: %v", stated)
	}
	if required, _ := limits["required"].([]any); len(required) != 3 {
		t.Errorf("the output schema does not require each of its three limits: %v", limits["required"])
	}

	// A meant of 22 CJK characters is 66 bytes: taught, stored and listed.
	host, ctx := &correctionHost{files: map[string][]byte{}}, context.Background()
	value, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "Kwin", "meant": strings.Repeat(cjk, 22), "revision": 0}))
	if err != nil {
		t.Fatal(err)
	}
	revision, rules, body := listed(t, value)
	if revision != 1 || len(rules) != 1 || rules[0].Meant != strings.Repeat(cjk, 22) || !bytes.Contains(host.files[correctionsPath], []byte(strings.Repeat(cjk, 22))) {
		t.Fatalf("a meant of 22 characters in 66 bytes was not taught and stored: %v", value)
	}
	said, _ := body["limits"].(map[string]any)
	if len(said) != 3 || said["rules"] != float64(maxCorrections) || said["characters"] != float64(maxCorrectionChars) || said["heard_words"] != float64(maxCorrectionWords) {
		t.Fatalf("the result's limits: %v", body["limits"])
	}
	if _, err = vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "rowen", "meant": strings.Repeat(cjk, 65), "revision": 1})); err == nil || err.Error() != "meant must be 1..64 characters" {
		t.Fatalf("a meant of 65 characters: %v", err)
	}
	if _, err = vocabularyCall(ctx, host, "vocabulary.forget", confirmed(map[string]any{"heard": strings.Repeat(cjk, 65), "revision": 1})); err == nil || err.Error() != "heard must be 1..64 characters" {
		t.Fatalf("forgetting a heard of 65 characters: %v", err)
	}
}

// The most a list can hold still fits where a list is kept and answered: its
// one stored page and the operations' declared result. A full list of sides
// of 64 four-byte characters is the most bytes stored; a meant of 64
// ampersands is the most a result spends on a character.
func TestTheLargestListFitsItsPageAndItsResult(t *testing.T) {
	stored, answered := correctionDocument{Revision: 1}, correctionDocument{Revision: 1}
	for i := 0; i < maxCorrections; i++ {
		heard := strings.Repeat(string(rune(0x1F600+i)), maxCorrectionChars)
		stored.Rules = append(stored.Rules, correctionRule{heard, strings.Repeat(string(rune(0x1F680+i)), maxCorrectionChars)})
		answered.Rules = append(answered.Rules, correctionRule{heard, strings.Repeat("&", maxCorrectionChars)})
	}
	for _, doc := range []correctionDocument{stored, answered} {
		encoded := doc.encode()
		if held, err := parseCorrections(encoded); err != nil || len(held.Rules) != maxCorrections {
			t.Fatalf("a full list of the longest sides is not a list: %v", err)
		}
		if len(encoded) > snapshotPageBytes {
			t.Fatalf("a full list of the longest sides is %d bytes, more than its page of %d", len(encoded), snapshotPageBytes)
		}
		changed := true
		result, err := json.Marshal(doc.result(&changed))
		if err != nil {
			t.Fatal(err)
		}
		for _, descriptor := range declaredPlugin().Descriptors() {
			if vocabularyOperation(descriptor.ID) && descriptor.MaxResultBytes < len(result) {
				t.Fatalf("%s answers at most %d bytes and a full list is %d", descriptor.ID, descriptor.MaxResultBytes, len(result))
			}
		}
	}
	if len(stored.Rules[0].Heard) != maxCorrectionBytes || len(stored.encode()) < maxCorrections*2*maxCorrectionBytes {
		t.Fatal("the fixture is not the longest list")
	}
}

// What was meant takes the two joiners where spelling puts them, as a
// speaker's label does; what was heard takes none. A rule that writes a
// Persian word with its non-joiner is taught, stored and listed with those
// exact bytes, and one whose joiner joins nothing is refused and never stored.
func TestWhatWasMeantIsJoinedAsItsSpellingJoinsIt(t *testing.T) {
	persian, joiner, nonJoiner := "\xd9\x85\xdb\x8c\xe2\x80\x8c\xd8\xae\xd9\x88\xd8\xa7\xd9\x87\xd9\x85", "\xe2\x80\x8d", "\xe2\x80\x8c"
	host, ctx := &correctionHost{files: map[string][]byte{}}, context.Background()
	value, err := vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "mikhaham", "meant": persian, "revision": 0}))
	if err != nil {
		t.Fatal(err)
	}
	if revision, rules, _ := listed(t, value); revision != 1 || len(rules) != 1 || rules[0].Meant != persian || !bytes.Contains(host.files[correctionsPath], []byte(`"meant":"`+persian+`"`)) {
		t.Fatalf("a word written with its non-joiner was not taught and stored as written: %v %q", value, host.files[correctionsPath])
	}
	if value, err = vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal(err)
	}
	if _, rules, _ := listed(t, value); len(rules) != 1 || rules[0].Meant != persian {
		t.Fatalf("the stored rule was not listed as written: %v", value)
	}
	stored := append([]byte(nil), host.files[correctionsPath]...)
	for _, unjoined := range []string{persian + nonJoiner, joiner + persian, "a " + joiner + "b", "a" + joiner + " b", "a" + joiner + nonJoiner + "b", joiner} {
		if _, err = vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": "rowen", "meant": unjoined, "revision": 1})); err == nil || err.Error() != "meant holds a joiner outside a word; a joiner is taken only inside a word" {
			t.Errorf("a meant of %+q: %v", unjoined, err)
		}
	}
	for _, heard := range []string{persian, "a" + joiner + "b", "a" + nonJoiner + "b"} {
		if _, err = vocabularyCall(ctx, host, "vocabulary.correct", confirmed(map[string]any{"heard": heard, "meant": "x", "revision": 1})); err == nil || err.Error() != "heard must not hold format characters, such as zero-width and direction marks" {
			t.Errorf("a heard of %+q: %v", heard, err)
		}
	}
	if !bytes.Equal(host.files[correctionsPath], stored) {
		t.Fatal("a refused rule changed the stored list")
	}
	// A stored list is held to the same rule: one whose joiner joins nothing is unreadable whole.
	for _, rule := range []correctionRule{{"rowen", "a" + joiner}, {"a" + nonJoiner + "b", "x"}} {
		host = &correctionHost{files: map[string][]byte{correctionsPath: correctionDocument{Revision: 1, Rules: []correctionRule{{"mikhaham", persian}, rule}}.encode()}}
		if _, err = vocabularyCall(ctx, host, "vocabulary.list", aiiosdk.Object(`{}`)); err == nil {
			t.Errorf("a stored list holding %+q -> %+q was listed", rule.Heard, rule.Meant)
		}
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
