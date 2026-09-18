package main

import (
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func enrollmentOperation(op string) bool {
	switch op {
	case "speaker.list", "speaker.enroll", "speaker.remove", "speaker.reset", "speaker.discard_capture", "speaker.upgrade_policy":
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
	if op != "speaker.list" {
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
func declaredPlugin() *aiiosdk.Plugin {
	p := aiiosdk.New("id.aiii.voice").DeclareSession()
	for _, name := range []string{"list", "enroll", "remove", "reset", "discard_capture", "upgrade_policy"} {
		op := "speaker." + name
		effect := aiiosdk.EffectsWriteLocal
		if name == "list" {
			effect = aiiosdk.EffectsReadInternal
		}
		p.Handle(op, func(aiiosdk.Call) (any, error) {
			return nil, errors.New("speaker operations require the resident plugin transport; listing, removal and reset do not require an open microphone")
		}).Describe(op, aiiosdk.Descriptor{
			Summary: map[string]string{"list": "List enrolled speakers, durable pending capture handles, recording readiness and any required policy upgrade with the microphone closed; never grants authority.", "enroll": "Enroll a selected durable capture after operator confirmation, including after microphone close or restart. Existing live-final selection remains supported.", "remove": "Remove one enrolled speaker after operator confirmation; no open microphone needed.", "reset": "Remove all enrolled speakers after operator confirmation; no open microphone needed.", "discard_capture": "Discard one pending enrollment capture after operator confirmation without removing any enrolled speaker.", "upgrade_policy": "With speech closed, propose the runtime-bound guided enrollment policy upgrade. Operator confirmation required; preserves existing usable speakers exactly and refuses incomplete profiles by name."}[name],
			Input:   "schemas/speaker-" + name + ".input.json", Output: "schemas/speaker.output.json", Effects: effect,
			Capabilities: []string{"fs.private"}, OperatorConfirms: name != "list", MaxResultBytes: 262144,
			Family: "speaker", Keywords: []string{"voice", "UID", "speaker identity", "enrollment"},
			// These are shown by the host's tools organ, not private README
			// instructions. IDs and final numbers are examples, never defaults.
			Examples: map[string][]string{
				"list":            {`{}`},
				"enroll":          {`{"capture_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","speaker_id":"sam","label":"Sam"}`},
				"remove":          {`{"speaker_id":"COPY_FROM_SPEAKER_LIST"}`},
				"reset":           {`{}`, `{"recovery":{"enrollment_sha256":"COPY_FROM_SPEAKER_LIST_RECOVERY","captures_sha256":"COPY_FROM_SPEAKER_LIST_RECOVERY"}}`},
				"discard_capture": {`{"capture_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}`},
				"upgrade_policy":  {`{}`},
			}[name],
		})
	}
	return p
}
