package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

type startupBrokenWriter struct{}

func (startupBrokenWriter) Write([]byte) (int, error) { return 0, errors.New("closed diagnostic") }

func TestStartupObservationIsNotReadinessOrAnEnvironmentDump(t *testing.T) {
	t.Setenv("AII_MODELS_DIR", "private-model-path-must-not-be-logged")
	t.Setenv("SECRET_TEST_VALUE", "secret-must-not-be-logged")
	var out bytes.Buffer
	if err := writeStartup(&out, "runtime-verification-progress", 2500*time.Millisecond, 1024, 990001); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(out.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if len(got) != 5 || got["component"] != "voice-carrier-startup" || got["phase"] != "runtime-verification-progress" || got["elapsed_ms"] != float64(2500) || got["files_verified"] != float64(1024) || got["bytes_verified"] != float64(990001) {
		t.Fatal(out.String())
	}
	for _, forbidden := range []string{"AII_VOICE_READY", "secret", "private-model-path", "environment", "models_loaded"} {
		if bytes.Contains(out.Bytes(), []byte(forbidden)) {
			t.Fatalf("startup observation claimed readiness or leaked ambient data: %s", out.String())
		}
	}
	if err := writeStartup(startupBrokenWriter{}, "carrier-enter", 0, 0, 0); err == nil {
		t.Fatal("diagnostic write error discarded by writer")
	}
}
