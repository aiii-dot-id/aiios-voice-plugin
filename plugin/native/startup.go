package main

import (
	"encoding/json"
	"io"
	"os"
	"time"
)

var startupBegan = time.Now()

// Stderr-only startup observations. No model names, paths, arguments, audio,
// transcripts or environment values enter this record. Never a readiness mark.
func writeStartup(w io.Writer, phase string, elapsed time.Duration, files int, bytes int64) error {
	return json.NewEncoder(w).Encode(struct {
		Component string `json:"component"`
		Phase     string `json:"phase"`
		ElapsedMS int64  `json:"elapsed_ms"`
		Files     int    `json:"files_verified"`
		Bytes     int64  `json:"bytes_verified"`
	}{"voice-carrier-startup", phase, elapsed.Milliseconds(), files, bytes})
}

func startupPhase(phase string, files int, bytes int64) {
	// A diagnostic write failure does not change model admission. The real
	// SDK readiness write and transport retain their own failure semantics.
	_ = writeStartup(os.Stderr, phase, time.Since(startupBegan), files, bytes)
}
