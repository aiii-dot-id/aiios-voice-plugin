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
	case "speaker.list", "speaker.enroll", "speaker.remove", "speaker.reset", "speaker.discard_capture", "speaker.upgrade_policy", "speaker.buckets", "speaker.associate", "speaker.forget":
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
	if op == "speaker.associate" || op == "speaker.forget" {
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
		var refs map[string]string
		if json.Unmarshal(raw, &refs) != nil || len(refs) != 2 {
			return errors.New("recovery requires the two observed digests from speaker.list")
		}
		for _, key := range []string{"enrollment_sha256", "captures_sha256"} {
			value, present := refs[key]
			if !present || (value != "absent" && !hexDigest(value)) {
				return errors.New("recovery requires an observed digest or absent for each store")
			}
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
	for _, name := range []string{"list", "enroll", "remove", "reset", "discard_capture", "upgrade_policy", "buckets", "associate", "forget"} {
		op := "speaker." + name
		effect := aiiosdk.EffectsWriteLocal
		if name == "list" || name == "buckets" {
			effect = aiiosdk.EffectsReadInternal
		}
		output := "schemas/speaker.output.json"
		maxResult := 262144
		if name == "buckets" || name == "associate" || name == "forget" {
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
			"buckets":         "List persistent anonymous speaker UUIDs and optional labels after microphone close or restart, with the exact revision for later naming.",
			"associate":       "Associate a chosen name or external ID with an existing speaker UUID at an exact registry revision after operator confirmation; no microphone required or authority granted.",
			"forget":          "Forget one anonymous speaker's acoustic profile and labels at an exact registry revision after operator confirmation. Historic transcripts are not deleted or reassigned; future observations may create a new UUID.",
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
				"associate":       {`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"1","display_label":"Chosen name"}`},
				"forget":          {`{"speaker_uuid":"12345678-1234-4234-8234-123456789abc","registry_revision":"1"}`},
			}[name],
		})
	}
	return p
}
