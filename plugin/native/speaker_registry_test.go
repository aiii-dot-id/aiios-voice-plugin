package main

import (
	"encoding/json"
	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
	"strings"
	"testing"
)

func TestSpeakerRegistryCompleteListFitsDeclaredBudget(t *testing.T) {
	rows := make([]map[string]any, 256)
	for i := range rows {
		evidence := make([]string, 8)
		for j := range evidence {
			evidence[j] = strings.Repeat("a", 64)
		}
		rows[i] = map[string]any{"speaker_uuid": "12345678-1234-4234-8234-123456789abc", "profile_available": true,
			"matching_ready": false, "matching_reason": "anonymous_profile_needs_corroboration",
			"display_label": strings.Repeat("\U0001F600", 128), "external_id": strings.Repeat("\U0001F601", 128),
			"evidence_sha256": evidence}
	}
	raw, err := json.Marshal(map[string]any{"registry_revision": "9007199254740991", "speakers": rows, "used_for_permissions": false, "session_open": false})
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) <= 262144 {
		t.Fatal("fixture must exceed old budget")
	}
	for _, d := range declaredPlugin().Descriptors() {
		if d.ID == "speaker.buckets" || d.ID == "speaker.associate" || d.ID == "speaker.forget" {
			if d.MaxResultBytes < len(raw) || d.MaxResultBytes > 1<<20 {
				t.Fatal("complete list exceeds declared bounded result", d.ID)
			}
		}
	}
}

func TestSpeakerRegistryDiscoveryAndConfirmedAssociation(t *testing.T) {
	if err := validateEnrollment("speaker.buckets", aiiosdk.Object(`{}`)); err != nil {
		t.Fatal(err)
	}
	good := hostEnrollmentArgs("speaker.associate")
	delete(good, "session_id")
	good["speaker_uuid"] = "12345678-1234-4234-8234-123456789abc"
	good["registry_revision"] = "1"
	good["display_label"] = "Chosen label"
	if err := validateEnrollment("speaker.associate", enrollmentObject(good)); err != nil {
		t.Fatal(err)
	}
	forget := hostEnrollmentArgs("speaker.forget")
	delete(forget, "session_id")
	forget["speaker_uuid"], forget["registry_revision"] = good["speaker_uuid"], "1"
	if err := validateEnrollment("speaker.forget", enrollmentObject(forget)); err != nil {
		t.Fatal(err)
	}
	forget["display_label"] = "not accepted"
	if validateEnrollment("speaker.forget", enrollmentObject(forget)) == nil {
		t.Fatal("forget accepted naming arguments")
	}
	for key, bad := range map[string]any{"speaker_uuid": "track-0", "registry_revision": "01", "display_label": 42, "_host_operator_act": nil, "_host_now_ms": nil} {
		test := map[string]any{}
		for k, v := range good {
			test[k] = v
		}
		test[key] = bad
		if validateEnrollment("speaker.associate", enrollmentObject(test)) == nil {
			t.Fatalf("bad %s accepted", key)
		}
	}
	q := snapshotQuery{settingsQuery: settingsQuery{ID: 1, SessionID: "s"}, Resource: "speaker_registry"}
	if !q.valid() || q.target() != speakerRegistryPath || q.limit() != 8<<20 {
		t.Fatal("registry resource not bounded")
	}
	for _, resource := range []string{"speaker_registry/../../enrollment", "uid/speakers.json", "speaker_registry:other"} {
		q.Resource = resource
		if q.valid() {
			t.Fatal("arbitrary registry resource")
		}
	}
}

func TestSpeakerLinkIsDiscoverableAndRequiresExactConfirmedArguments(t *testing.T) {
	args := hostEnrollmentArgs("speaker.link")
	delete(args, "session_id")
	args["speaker_uuid"] = "12345678-1234-4234-8234-123456789abc"
	args["target_uuid"] = "12345678-1234-4234-8234-123456789abd"
	args["registry_revision"] = "2"
	if err := validateEnrollment("speaker.link", enrollmentObject(args)); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"speaker_uuid", "target_uuid", "registry_revision", "_host_operator_act", "_host_now_ms"} {
		copy := map[string]any{}
		for k, v := range args {
			copy[k] = v
		}
		delete(copy, key)
		if validateEnrollment("speaker.link", enrollmentObject(copy)) == nil {
			t.Fatal("missing field accepted", key)
		}
	}
	found := false
	for _, d := range declaredPlugin().Descriptors() {
		if d.ID == "speaker.link" {
			found = true
			if !d.OperatorConfirms || d.Input != "schemas/speaker-link.input.json" || d.Output != "schemas/speaker-buckets.output.json" || len(d.Examples) == 0 || d.Summary == "" {
				t.Fatal("link contract incomplete", d)
			}
		}
	}
	if !found {
		t.Fatal("link absent from discovery")
	}
}
