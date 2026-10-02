package main

import (
	"strings"
	"testing"
)

func TestRecoveryRequiresExactReferencesAndConfirmation(t *testing.T) {
	for _, bad := range []string{"none", "registry", "missing-act", "missing-digest", "bad-digest", "extra-field", "array", "other-operation",
		"registry-absent", "registry-bad-digest", "registry-extra-field", "registry-missing-digest"} {
		a := hostEnrollmentArgs("speaker.reset")
		refs := map[string]string{"enrollment_sha256": strings.Repeat("a", 64), "captures_sha256": "absent"}
		a["recovery"] = refs
		op := "speaker.reset"
		switch bad {
		case "missing-act":
			delete(a, "_host_operator_act")
		case "missing-digest":
			delete(refs, "captures_sha256")
		case "bad-digest":
			refs["captures_sha256"] = "../../profile"
		case "extra-field":
			refs["path"] = "uid/enrollment.json"
		case "array":
			a["recovery"] = []string{"absent", "absent"}
		case "other-operation":
			op = "speaker.list"
		}
		// The registry digest is named exactly when speaker.list reported it.
		if strings.HasPrefix(bad, "registry") {
			refs["speaker_registry_sha256"] = strings.Repeat("b", 64)
		}
		switch bad {
		case "registry-absent":
			refs["speaker_registry_sha256"] = "absent"
		case "registry-bad-digest":
			refs["speaker_registry_sha256"] = "uid/speakers.json"
		case "registry-extra-field":
			refs["path"] = "uid/speakers.json"
		case "registry-missing-digest":
			delete(refs, "enrollment_sha256")
		}
		err := validateEnrollment(op, enrollmentObject(a))
		if (err == nil) != (bad == "none" || bad == "registry") {
			t.Fatalf("case %s: %v", bad, err)
		}
	}
}

func TestRecoveryArchiveCannotBecomeArbitraryOrMutableStorage(t *testing.T) {
	hash := strings.Repeat("a", 64)
	q := snapshotQuery{settingsQuery: settingsQuery{1, "s"}, Resource: "recovery:" + hash}
	if !q.valid() || q.target() != "uid/recovery-"+hash+".json" || q.limit() != 12<<20 {
		t.Fatal("archive target not fixed")
	}
	q.Action = "publish"
	q.Upload = strings.Repeat("b", 64)
	q.SHA = hash
	q.Absent = true
	if !q.valid() {
		t.Fatal("immutable first publication refused")
	}
	q.Absent = false
	q.Expected = hash
	if !q.valid() {
		t.Fatal("exact re-attestation refused")
	}
	q.Expected = strings.Repeat("c", 64)
	if q.valid() {
		t.Fatal("archive overwrite admitted")
	}
	q.Expected = hash
	q.SHA = strings.Repeat("d", 64)
	if q.valid() {
		t.Fatal("foreign content admitted to archive name")
	}
	for _, path := range []string{"recovery:", "recovery:../enrollment", "recovery:" + strings.Repeat("A", 64)} {
		q = snapshotQuery{settingsQuery: settingsQuery{1, "s"}, Resource: path}
		if q.valid() {
			t.Fatal("arbitrary archive accepted", path)
		}
	}
}
