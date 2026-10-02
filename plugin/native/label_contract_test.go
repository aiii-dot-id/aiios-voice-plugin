package main

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"regexp"
	"testing"
	"unicode/utf8"
)

func TestLabelVectorsMatchTheShippedSchema(t *testing.T) {
	var document struct {
		Properties map[string]struct {
			Type                 string
			MinLength, MaxLength int
			Pattern              string
		}
	}
	raw, err := os.ReadFile("schemas/speaker-enroll.input.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	label := document.Properties["label"]
	if label.Type != "string" || label.MinLength != 1 || label.MaxLength != 128 {
		t.Fatal("scalar contract differs")
	}
	pattern, err := regexp.Compile(label.Pattern)
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Name     string
		UTF8     string `json:"utf8_hex"`
		Accepted bool
	}
	raw, err = os.ReadFile("../../spec/uid_label_vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &rows); err != nil {
		t.Fatal(err)
	}
	if len(rows) != 17 {
		t.Fatal("missing vectors")
	}
	for _, row := range rows {
		text, err := hex.DecodeString(row.UTF8)
		if err != nil {
			t.Fatal(err)
		}
		// The host's schema subset counts runes and uses RE2. Invalid UTF-8
		// vectors test the transport boundary before JSON string admission.
		n := utf8.RuneCount(text)
		accepted := utf8.Valid(text) && n >= label.MinLength && n <= label.MaxLength && pattern.Match(text)
		if accepted != row.Accepted {
			t.Errorf("schema label disagreement %s: %v", row.Name, accepted)
		}
	}
}
