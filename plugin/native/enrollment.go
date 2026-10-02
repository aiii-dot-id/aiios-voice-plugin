package main

import (
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func enrollmentOperation(op string) bool {
	switch op {
	case "speaker.list", "speaker.enroll", "speaker.remove", "speaker.reset", "speaker.discard_capture", "speaker.upgrade_policy", "speaker.buckets", "speaker.associate", "speaker.forget", "speaker.link":
		return true
	}
	return false
}
func validateEnrollment(op string, args aiiosdk.Object) error {
	if !enrollmentOperation(op) {
		return nil
	}
	var fields map[string]json.RawMessage
	if len(args) > 4096 || json.Unmarshal(args, &fields) != nil || fields == nil {
		return errors.New("bounded enrollment arguments required")
	}
	allowed := map[string]bool{"session_id": true}
	if op == "speaker.associate" || op == "speaker.forget" || op == "speaker.link" {
		for _, name := range []string{"speaker_uuid", "registry_revision"} {
			allowed[name] = true
		}
		id, ok := args.String("speaker_uuid")
		if !ok || !speakerUUID(id) {
			return errors.New("canonical speaker_uuid from speaker.buckets required")
		}
		raw, ok := args.String("registry_revision")
		n, err := strconv.ParseUint(raw, 10, 64)
		if !ok || err != nil || n > 9007199254740991 || strconv.FormatUint(n, 10) != raw {
			return errors.New("exact registry_revision from speaker.buckets required")
		}
		if op == "speaker.link" {
			allowed["target_uuid"] = true
			if target, ok := args.String("target_uuid"); !ok || !speakerUUID(target) {
				return errors.New("canonical target_uuid from speaker.buckets required")
			}
		}
		if op == "speaker.associate" {
			allowed["display_label"], allowed["external_id"] = true, true
			label, ok := args.String("display_label")
			if !ok || len(label) > 512 {
				return errors.New("display_label required; empty clears the current label")
			}
			if _, present := fields["external_id"]; present {
				if value, ok := args.String("external_id"); !ok || len(value) > 512 {
					return errors.New("bounded external_id string required")
				}
			}
		}
	}
	if op == "speaker.reset" {
		allowed["recovery"] = true
	}
	if op == "speaker.enroll" || op == "speaker.remove" {
		allowed["speaker_id"] = true
	}
	if op == "speaker.enroll" {
		allowed["label"] = true
		allowed["finals"] = true
	}
	if op == "speaker.enroll" || op == "speaker.discard_capture" {
		allowed["capture_id"] = true
	}
	for name := range fields {
		// The host strips caller-supplied _host* keys and injects its own
		// metadata on every tool invocation. These are not operation fields.
		// Tolerating the namespace does not waive the confirmation below.
		if strings.HasPrefix(name, "_host") {
			continue
		}
		if !allowed[name] {
			return errors.New("unknown enrollment argument")
		}
	}
	if raw, ok := fields["recovery"]; ok {
		if err := validateRecovery(raw); err != nil {
			return err
		}
	}
	_, guided := fields["capture_id"]
	if guided {
		id, ok := args.String("capture_id")
		if !ok || len(id) != 64 || strings.Trim(id, "0123456789abcdef") != "" {
			return errors.New("invalid capture_id; copy an exact pending_captures handle from speaker.list")
		}
		if _, mixed := fields["finals"]; mixed {
			return errors.New("capture_id and finals are mutually exclusive")
		}
	} else if op == "speaker.discard_capture" {
		return errors.New("capture_id required; call speaker.list")
	}
	// A durable capture is independent of the microphone/session lifecycle.
	// Retain the existing finalized-recording path for unchanged checkpoints.
	if _, supplied := fields["session_id"]; !supplied {
		if op == "speaker.enroll" && !guided {
			return errors.New("missing session_id; call speaker.list and provide either a durable capture_id or a session_id with eligible finals")
		}
	} else if sid, ok := args.String("session_id"); !ok || sid == "" || len(sid) > 128 {
		return errors.New("invalid session_id: expected a non-empty string of at most 128 bytes; call speaker.list to obtain the current session_id")
	}
	if op != "speaker.list" && op != "speaker.buckets" {
		var nowMillis int64
		if json.Unmarshal(fields["_host_now_ms"], &nowMillis) != nil || nowMillis <= 0 {
			return errors.New("host invocation time required")
		}
		stamp, ok := aiiosdk.OperatorAct(args)
		if !ok || len(stamp.ID) > 128 {
			return errors.New("host operator confirmation required")
		}
		when, e := time.Parse(time.RFC3339Nano, stamp.ConfirmedAt)
		now := time.UnixMilli(nowMillis)
		if e != nil || when.Before(now.Add(-10*time.Minute)) || when.After(now.Add(time.Minute)) {
			return errors.New("host operator confirmation invalid or expired")
		}
	}
	return nil
}
func speakerUUID(s string) bool {
	if len(s) != 36 || s[8] != '-' || s[13] != '-' || s[18] != '-' || s[23] != '-' || s[14] != '4' || !strings.ContainsRune("89ab", rune(s[19])) {
		return false
	}
	for i, c := range s {
		if i != 8 && i != 13 && i != 18 && i != 23 && !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}
func declaredPlugin() *aiiosdk.Plugin {
	p := aiiosdk.New("id.aiii.voice").DeclareSession()
	for _, name := range []string{"list", "enroll", "remove", "reset", "discard_capture", "upgrade_policy", "buckets", "associate", "forget", "link"} {
		op := "speaker." + name
		effect := aiiosdk.EffectsWriteLocal
		if name == "list" || name == "buckets" {
			effect = aiiosdk.EffectsReadInternal
		}
		output := "schemas/speaker.output.json"
		maxResult := 262144
		if name == "buckets" || name == "associate" || name == "forget" || name == "link" {
			output = "schemas/speaker-buckets.output.json"
			// 256 rows can each carry two 128-scalar labels. Four-byte UTF-8
			// names alone exceed the legacy result budget; retain the full list.
			maxResult = 1 << 20
		}
		summaries := map[string]string{
			"list":            "List enrolled speakers, durable captures and readiness without an open microphone.",
			"enroll":          "Enroll a selected durable capture after operator confirmation; live-final selection is also supported.",
			"remove":          "Remove one enrolled speaker after operator confirmation without an open microphone.",
			"reset":           "Reset enrolled speakers after operator confirmation; no microphone required.",
			"discard_capture": "Discard one pending capture after operator confirmation without changing enrolled speakers.",
			"upgrade_policy":  "With speech closed and operator confirmation, upgrade to the runtime-bound policy, preserving usable speakers and refusing incomplete profiles.",
			"buckets":         "List persistent speaker UUIDs, labels, linked legacy enrollment IDs, acoustic matching readiness and the exact revision for later naming. Stored anonymous singleton profiles retain history but are not ready for recognition. No open microphone required.",
			"link":            "After operator confirmation, correct two acoustic UUIDs known to be the same person to one future output UUID without averaging profiles, relaxing matching or rewriting history. Self-target undoes the correction; no microphone required.",
			"associate":       "Associate a chosen name or external ID with an existing speaker UUID at an exact registry revision after operator confirmation; no microphone required or authority granted.",
			"forget":          "Forget one speaker UUID's profile and labels at an exact revision after operator confirmation. Remove a linked enrollment first with speaker.remove. Historic transcripts are not deleted or reassigned; future observations after explicit removal may create a new UUID.",
		}
		p.Handle(op, func(aiiosdk.Call) (any, error) {
			return nil, errors.New("speaker operations require the resident plugin transport; listing, removal and reset do not require an open microphone")
		}).Describe(op, aiiosdk.Descriptor{
			Summary: summaries[name],
			Input:   "schemas/speaker-" + name + ".input.json", Output: output, Effects: effect,
			Capabilities: []string{"fs.private"}, OperatorConfirms: name != "list" && name != "buckets", MaxResultBytes: maxResult,
			Family: "speaker", Keywords: []string{"voice", "UID", "speaker identity", "enrollment"},
			// These are shown by the host's tools organ, not private README
			// instructions. IDs and final numbers are examples, never defaults.
			Examples: map[string][]string{
				"list":            {`{}`},
				"enroll":          {`{"capture_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","speaker_id":"sam","label":"Sam"}`},
				"remove":          {`{"speaker_id":"COPY_FROM_SPEAKER_LIST"}`},
				"reset":           {`{}`, `{"recovery":{"enrollment_sha256":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","captures_sha256":"absent"}}`},
				"discard_capture": {`{"capture_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}`},
				"upgrade_policy":  {`{}`},
				"buckets":         {`{}`},
				"link":            {`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","target_uuid":"12345678-1234-4234-8234-123456789abd","registry_revision":"2"}`},
				"associate":       {`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"1","display_label":"Chosen name"}`},
				"forget":          {`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"1"}`},
			}[name],
		})
	}
	for _, name := range []string{"record", "status", "list", "delete", "prune"} {
		op := "recording." + name
		maxResultBytes := 65536
		if name == "list" {
			// 1,024 canonical IDs and sizes exceed 64 KiB at the host boundary.
			maxResultBytes = 131072
		}
		effect := aiiosdk.EffectsReadInternal
		if name == "record" || name == "delete" || name == "prune" {
			effect = aiiosdk.EffectsWriteLocal
		}
		summary := "Read the state and private receipt of the most recent waveform recording; never returns audio."
		if name == "record" {
			summary = "Save the last buffered microphone recording to a private WAV file now and return its receipt. No STT final, pre-arming, or separate confirmation required."
		} else if name == "list" {
			summary = "List bounded private WAV identifiers and sizes, with truncated=true for an incomplete inventory; never returns audio."
		} else if name == "delete" {
			summary = "Delete one private WAV by an exact recording_id from its save receipt or list, even when the listing is truncated; operator confirmation required."
		} else if name == "prune" {
			summary = "After operator confirmation, remove only expired interrupted private recording and speaker-store uploads; active or unrecognized files are retained."
		}
		p.Handle(op, func(aiiosdk.Call) (any, error) {
			return nil, errors.New("recording operations require the resident plugin transport")
		}).Describe(op, aiiosdk.Descriptor{
			Summary: summary, Input: "schemas/recording-" + name + ".input.json",
			Output: recordingOutput(name), Effects: effect,
			Capabilities: []string{"fs.private"}, OperatorConfirms: name == "delete" || name == "prune",
			MaxResultBytes: maxResultBytes, Family: "recording",
			Keywords: []string{"voice", "private waveform", "speaker diagnostics"},
			Examples: recordingExamples(name),
		})
	}
	return p
}

func recordingOutput(name string) string {
	if name == "list" || name == "delete" || name == "prune" {
		return "schemas/recording-" + name + ".output.json"
	}
	return "schemas/recording.output.json"
}

func recordingExamples(name string) []string {
	if name == "delete" {
		return []string{`{"recording_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}`}
	}
	return []string{`{}`}
}
