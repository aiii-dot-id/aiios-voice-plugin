package main

import (
	"strings"
	"testing"
)

func TestEnrollmentSessionDiagnosticDescribesArgumentsNotLiveState(t *testing.T) {
	for _, op := range []string{"speaker.list", "speaker.enroll", "speaker.remove", "speaker.reset"} {
		t.Run(op, func(t *testing.T) {
			a := hostEnrollmentArgs(op)
			delete(a, "session_id")
			err := validateEnrollment(op, enrollmentObject(a))
			if op != "speaker.enroll" {
				if err != nil {
					t.Fatalf("session discovery was stranded: %v", err)
				}
			} else if err == nil || !strings.HasPrefix(err.Error(), "missing session_id;") || !strings.Contains(err.Error(), "call speaker.list") {
				t.Fatalf("missing argument was reported as live state: %v", err)
			}
			for _, bad := range []any{nil, false, 42, "", strings.Repeat("private-id-do-not-log-", 7), []string{"s"}} {
				a["session_id"] = bad
				err := validateEnrollment(op, enrollmentObject(a))
				if err == nil || !strings.HasPrefix(err.Error(), "invalid session_id:") || !strings.Contains(err.Error(), "call speaker.list") {
					t.Fatalf("invalid argument was not classified: %v", err)
				}
				if strings.Contains(err.Error(), "private-id-do-not-log") || strings.Contains(err.Error(), "open speech session required") {
					t.Fatalf("diagnostic leaked a value or invented a live observation: %v", err)
				}
			}
			for _, valid := range []string{"s", strings.Repeat("x", 128)} {
				a["session_id"] = valid
				if err := validateEnrollment(op, enrollmentObject(a)); err != nil {
					t.Fatalf("valid shape refused before live worker gate: %v", err)
				}
			}
		})
	}
}
