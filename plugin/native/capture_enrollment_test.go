package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiospkg"
)

func TestAllSpeakerSchemasAreShippedAndSDKCompilable(t *testing.T) {
	for _, descriptor := range declaredPlugin().Descriptors() {
		if !enrollmentOperation(descriptor.ID) {
			continue
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
			if descriptor.ID == "speaker.enroll" || descriptor.ID == "speaker.discard_capture" {
				stamp := hostEnrollmentArgs(descriptor.ID)
				args["_host_now_ms"] = stamp["_host_now_ms"]
				args["_host_operator_act"] = stamp["_host_operator_act"]
				if err := validateEnrollment(descriptor.ID, enrollmentObject(args)); err != nil {
					t.Fatalf("published capture example invalid: %v", err)
				}
			}
		}
	}
}

func TestPolicyUpgradeRequiresHostConfirmationAndNoCallerPolicy(t *testing.T) {
	args := hostEnrollmentArgs("speaker.upgrade_policy")
	delete(args, "session_id")
	delete(args, "finals")
	delete(args, "speaker_id")
	delete(args, "label")
	if err := validateEnrollment("speaker.upgrade_policy", enrollmentObject(args)); err != nil {
		t.Fatal(err)
	}
	args["threshold"] = 0.1
	if validateEnrollment("speaker.upgrade_policy", enrollmentObject(args)) == nil {
		t.Fatal("caller policy override admitted")
	}
	delete(args, "threshold")
	delete(args, "_host_operator_act")
	if validateEnrollment("speaker.upgrade_policy", enrollmentObject(args)) == nil {
		t.Fatal("unconfirmed policy upgrade admitted")
	}
}

func TestDurableCaptureEnrollmentDoesNotNeedLiveSession(t *testing.T) {
	for _, op := range []string{"speaker.enroll", "speaker.discard_capture"} {
		a := hostEnrollmentArgs(op)
		delete(a, "session_id")
		delete(a, "finals")
		a["capture_id"] = strings.Repeat("a", 64)
		if err := validateEnrollment(op, enrollmentObject(a)); err != nil {
			t.Fatal("closed-session capture stranded", err)
		}
		a["finals"] = []int{1}
		if validateEnrollment(op, enrollmentObject(a)) == nil {
			t.Fatal("ambiguous evidence selection admitted")
		}
		delete(a, "finals")
		delete(a, "_host_operator_act")
		if validateEnrollment(op, enrollmentObject(a)) == nil {
			t.Fatal("capture bypassed confirmation")
		}
		a = hostEnrollmentArgs(op)
		delete(a, "session_id")
		delete(a, "finals")
		a["capture_id"] = "../recording"
		if validateEnrollment(op, enrollmentObject(a)) == nil {
			t.Fatal("arbitrary capture path admitted")
		}
	}
}
