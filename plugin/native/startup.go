package main

import (
	"encoding/json"
	"io"
	"os"
	"time"
)

var startupBegan = time.Now()

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

	_ = writeStartup(os.Stderr, phase, time.Since(startupBegan), files, bytes)
}
