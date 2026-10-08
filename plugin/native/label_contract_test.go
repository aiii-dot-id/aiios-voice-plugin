package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"testing"
	"unicode"
	"unicode/utf8"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// labelVectors is spec/uid_label_vectors.json, the file the worker's label
// rule is held to as well (runtime/native_uid/label_contract_test.cpp).
type labelVectors struct {
	Labels []struct {
		Name     string
		UTF8     string `json:"utf8_hex"`
		Accepted bool
	}
	Refused []codePointRange `json:"refused_code_points"`
	Joiners []string
	Spaces  []codePointRange
}

type codePointRange struct {
	First    string `json:"first"`
	Last     string `json:"last"`
	Category string `json:"category,omitempty"`
}

func codePoints(t *testing.T, ranges []codePointRange) map[rune]bool {
	t.Helper()
	set := map[rune]bool{}
	for _, row := range ranges {
		first, ferr := strconv.ParseUint(row.First, 16, 32)
		last, lerr := strconv.ParseUint(row.Last, 16, 32)
		if ferr != nil || lerr != nil || first > last || last > unicode.MaxRune {
			t.Fatalf("range %q..%q is unreadable", row.First, row.Last)
		}
		for r := rune(first); r <= rune(last); r++ {
			set[r] = true
		}
	}
	return set
}

// readLabelVectors returns the vectors with their three tables: what a label
// may not hold, the two joiners that are excepted inside a word, and spaces.
func readLabelVectors(t *testing.T) (vectors labelVectors, refused, joiners, spaces map[rune]bool) {
	t.Helper()
	raw, err := os.ReadFile("../../spec/uid_label_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &vectors); err != nil || len(vectors.Labels) < 63 {
		t.Fatal("label vectors incomplete", err)
	}
	refused, spaces = codePoints(t, vectors.Refused), codePoints(t, vectors.Spaces)
	joiners = map[rune]bool{}
	for _, text := range vectors.Joiners {
		joiners[rune(mustHex(t, text))] = true
	}
	if len(refused) != 237 || len(spaces) != 17 || len(joiners) != 2 || !joiners[0x200C] || !joiners[0x200D] {
		t.Fatalf("label tables hold %d refused, %d spaces, %d joiners", len(refused), len(spaces), len(joiners))
	}
	return vectors, refused, joiners, spaces
}

func mustHex(t *testing.T, text string) uint64 {
	t.Helper()
	n, err := strconv.ParseUint(text, 16, 32)
	if err != nil {
		t.Fatalf("code point %q is unreadable", text)
	}
	return n
}

// labelPatterns are the two places the host is given the label rule: the
// label of speaker.enroll and the display_label of speaker.associate.
func labelPatterns(t *testing.T) map[string]*regexp.Regexp {
	t.Helper()
	patterns, written := map[string]*regexp.Regexp{}, ""
	for _, shown := range []struct {
		file, name string
		least      int
	}{{"schemas/speaker-enroll.input.json", "label", 1}, {"schemas/speaker-associate.input.json", "display_label", 0}} {
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
		label := document.Properties[shown.name]
		// An empty display_label clears the label, so only enrollment has a least length.
		if label.Type != "string" || label.MinLength != shown.least || label.MaxLength != 128 || label.Pattern == "" {
			t.Fatalf("%s %s: scalar contract differs: %+v", shown.file, shown.name, label)
		}
		if written != "" && label.Pattern != written {
			t.Fatalf("%s %s is held to another pattern than a speaker's label", shown.file, shown.name)
		}
		written = label.Pattern
		// The pattern reaches the host as these characters. It names the two
		// joiners by themselves and nothing else that cannot be seen.
		for _, r := range written {
			if r != 0x200C && r != 0x200D && (r < 0x20 || r > 0x7E) {
				t.Fatalf("%s %s: the pattern holds U+%04X", shown.file, shown.name, r)
			}
		}
		pattern, err := regexp.Compile(written)
		if err != nil {
			t.Fatal(err)
		}
		patterns[shown.file+" "+shown.name] = pattern
	}
	return patterns
}

func TestLabelVectorsMatchTheShippedSchema(t *testing.T) {
	vectors, _, _, _ := readLabelVectors(t)
	patterns := labelPatterns(t)
	enroll := patterns["schemas/speaker-enroll.input.json label"]
	for _, row := range vectors.Labels {
		text, err := hex.DecodeString(row.UTF8)
		if err != nil {
			t.Fatal(err)
		}
		// The host's schema subset counts runes and uses RE2. Invalid UTF-8
		// vectors test the transport boundary before JSON string admission.
		n := utf8.RuneCount(text)
		accepted := utf8.Valid(text) && n >= 1 && n <= 128 && enroll.Match(text)
		if accepted != row.Accepted {
			t.Errorf("schema label disagreement %s: %v", row.Name, accepted)
		}
		// The carrier's own rule says the same of every row's characters, and
		// of bytes that are not UTF-8 without being told that they are not.
		if judged := n >= 1 && n <= 128 && readableLabel(string(text)); judged != row.Accepted {
			t.Errorf("carrier label disagreement %s: %v", row.Name, judged)
		}
	}
}

// A label is a person's name as an operator reads and confirms it. The
// carrier's rule and the schemas' pattern are asked about every code point a
// label may not hold, in every place it could stand, and about the two joiners
// beside every space; the carrier's rule about every code point there is.
func TestALabelHoldsNothingAnOperatorCannotRead(t *testing.T) {
	vectors, refused, joiners, spaces := readLabelVectors(t)
	patterns := labelPatterns(t)

	// One table of the four categories serves a correction's text and a label.
	if corrections := readCorrectionVectors(t); !reflect.DeepEqual(vectors.Refused, func() []codePointRange {
		var rows []codePointRange
		for _, row := range corrections.Refused {
			rows = append(rows, codePointRange{row.First, row.Last, row.Category})
		}
		return rows
	}()) {
		t.Fatal("the label vectors and the correction vectors refuse different code points")
	}
	// And one pair of tables says where a joiner is spelling, for a label and for what a correction meant.
	if corrections := readCorrectionVectors(t); !reflect.DeepEqual(vectors.Joiners, corrections.Joiners) || !reflect.DeepEqual(vectors.Spaces, corrections.Spaces) {
		t.Fatal("the label vectors and the correction vectors differ on the joiners or on what a space is")
	}

	judge := func(label string, want bool, why string) {
		t.Helper()
		if readableLabel(label) != want {
			t.Errorf("carrier: %s (%+q): taken %v", why, label, !want)
		}
		for where, pattern := range patterns {
			if pattern.MatchString(label) != want {
				t.Errorf("%s: %s (%+q): taken %v", where, why, label, !want)
			}
		}
	}
	for r := rune(0); r <= unicode.MaxRune; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue // surrogates are not characters: no text holds one
		}
		if refused[r] != unicode.In(r, unicode.Cc, unicode.Cf, unicode.Zl, unicode.Zp) || spaces[r] != unicode.Is(unicode.Zs, r) {
			t.Fatalf("U+%04X: this toolchain's Unicode %s and the vectors differ; the worker's lists, the vectors and the carrier move together", r, unicode.Version)
		}
		c := string(r)
		// Alone between two letters: text, or one of the two joiners.
		if got, want := readableLabel("a"+c+"b"), !refused[r] || joiners[r]; got != want {
			t.Fatalf("U+%04X between two letters: taken %v", r, got)
		}
		// On both sides of a joiner: anything a label holds but a space.
		if got, want := readableLabel(c+string(rune(0x200D))+c), !refused[r] && !spaces[r]; got != want {
			t.Fatalf("U+%04X on both sides of a joiner: taken %v", r, got)
		}
		if !refused[r] {
			continue
		}
		judge(c+"ab", false, "first")
		judge("ab"+c, false, "last")
		judge(c, false, "alone")
		judge("a"+c+c+"b", false, "doubled")
		judge("a "+c+"b", false, "after a space")
		judge("a"+c+" b", false, "before a space")
		judge("a"+c+"b", joiners[r], "between two letters")
	}
	for space := range spaces {
		judge("a"+string(space)+"b", true, "a space between two letters")
		for joiner := range joiners {
			judge("a"+string(space)+string(joiner)+"b", false, "a joiner after a space")
			judge("a"+string(joiner)+string(space)+"b", false, "a joiner before a space")
		}
	}
	for first := range joiners {
		for second := range joiners {
			judge("a"+string(first)+string(second)+"b", false, "two joiners together")
			judge("a"+string(first)+"b"+string(second)+"c", true, "two joiners, a letter between them")
		}
	}
	// The code points on either side of each refused range are text.
	for _, row := range vectors.Refused {
		for _, r := range []rune{rune(mustHex(t, row.First)) - 1, rune(mustHex(t, row.Last)) + 1} {
			if r >= 0 && !refused[r] {
				judge("a"+string(r)+"b", true, "beside a refused range")
			}
		}
	}
	judge("", true, "nothing at all (its length is the schema's and the worker's to refuse)")
}

// The carrier judges a label when it is given, before the worker is asked.
func TestAGivenLabelIsJudgedByTheCarrier(t *testing.T) {
	override, joined := "a"+string(rune(0x202E))+"b", "a"+string(rune(0x200C))+"b"
	call := func(op, key, label string) error {
		args := hostEnrollmentArgs(op)
		if op == "speaker.associate" {
			delete(args, "session_id")
			args["speaker_uuid"], args["registry_revision"] = "12345678-1234-4234-8234-123456789abc", "1"
		}
		args[key] = label
		return validateEnrollment(op, enrollmentObject(args))
	}
	for _, given := range []struct{ op, key string }{{"speaker.enroll", "label"}, {"speaker.associate", "display_label"}} {
		if err := call(given.op, given.key, joined); err != nil {
			t.Errorf("%s refused a label with a non-joiner inside a word: %v", given.op, err)
		}
		for _, bad := range []string{override, "a" + string(rune(0x200B)) + "b", "a" + string(rune(0x2028)) + "b", "a\x7fb", joined + string(rune(0x200D)), string(rune(0xFEFF)) + "ab"} {
			if err := call(given.op, given.key, bad); err == nil || err.Error() != given.key+" holds a control or format character; a joiner is taken only inside a word" {
				t.Errorf("%s %+q: %v", given.op, bad, err)
			}
		}
		// Bytes that are not UTF-8 are refused in those words.
		args := hostEnrollmentArgs(given.op)
		if given.op == "speaker.associate" {
			delete(args, "session_id")
			args["speaker_uuid"], args["registry_revision"] = "12345678-1234-4234-8234-123456789abc", "1"
		}
		args[given.key] = "Chosen?"
		sent := bytes.Replace(enrollmentObject(args), []byte("Chosen?"), []byte("Chosen\xff"), 1)
		if err := validateEnrollment(given.op, aiiosdk.Object(sent)); err == nil || err.Error() != "text must be valid UTF-8" {
			t.Errorf("%s took a label holding a byte that is not UTF-8: %v", given.op, err)
		}
	}
	// An empty display_label clears the label and is not a label to judge.
	if err := call("speaker.associate", "display_label", ""); err != nil {
		t.Errorf("clearing a label was refused: %v", err)
	}
}
