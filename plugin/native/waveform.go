package main

import (
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func waveformOperation(op string) bool {
	return op == "recording.record" || op == "recording.status" || op == "recording.list" || op == "recording.delete" || op == "recording.prune"
}

func validateWaveform(op string, args aiiosdk.Object) error {
	if !waveformOperation(op) {
		return nil
	}
	var fields map[string]json.RawMessage
	if len(args) > 4096 || json.Unmarshal(args, &fields) != nil || fields == nil {
		return errors.New("bounded recording arguments required")
	}
	for key := range fields {
		if strings.HasPrefix(key, "_host") {
			continue
		}
		if op == "recording.delete" && key == "recording_id" {
			var id string
			if json.Unmarshal(fields[key], &id) != nil || !hexDigest(id) {
				return errors.New("canonical recording_id required")
			}
			continue
		}
		return errors.New("unknown recording argument; no audio bytes or path accepted")
	}
	if op == "recording.delete" {
		if _, ok := fields["recording_id"]; !ok {
			return errors.New("recording_id required")
		}
	}
	if op == "recording.delete" || op == "recording.prune" {
		var nowMillis int64
		if json.Unmarshal(fields["_host_now_ms"], &nowMillis) != nil || nowMillis <= 0 {
			return errors.New("host invocation time required")
		}
		stamp, ok := aiiosdk.OperatorAct(args)
		if !ok || len(stamp.ID) > 128 {
			return errors.New("host operator confirmation required")
		}
		when, err := time.Parse(time.RFC3339Nano, stamp.ConfirmedAt)
		now := time.UnixMilli(nowMillis)
		if err != nil || when.Before(now.Add(-10*time.Minute)) || when.After(now.Add(time.Minute)) {
			return errors.New("host operator confirmation invalid or expired")
		}
	}
	return nil
}
