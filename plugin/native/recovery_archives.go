package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

const (
	recoveryListSummary      = "List the speaker recovery archives: copies of speaker files set aside by a confirmed recovery, holding voiceprints and names. Each has an archive_sha256, its size and what it holds; truncated=true means the list is incomplete. Never returns their contents; no microphone required."
	recoveryDeleteSummary    = "After operator confirmation, delete one speaker recovery archive by its exact archive_sha256 from speaker.recovery_list. The voiceprints and names it holds are gone for good; current speakers are not changed."
	recoveryArchiveRequired  = "exact archive_sha256 from speaker.recovery_list required"
	recoveryUnknownArgument  = "unknown recovery archive argument; no path or contents accepted"
	recoveryBoundedArguments = "bounded recovery archive arguments required"
)

const (
	recoveryDirectory = "uid"
	recoveryPrefix    = "recovery-"
	recoverySuffix    = ".json"
)

func recoveryArchiveOperation(op string) bool {
	return op == "speaker.recovery_list" || op == "speaker.recovery_delete"
}

func validateRecoveryArchive(op string, args aiiosdk.Object) error {
	if !recoveryArchiveOperation(op) {
		return nil
	}
	var fields map[string]json.RawMessage
	if len(args) > 4096 || json.Unmarshal(args, &fields) != nil || fields == nil {
		return errors.New(recoveryBoundedArguments)
	}
	for key := range fields {
		if strings.HasPrefix(key, "_host") {
			continue
		}
		if op == "speaker.recovery_delete" && key == "archive_sha256" {
			var id string
			if json.Unmarshal(fields[key], &id) != nil || !hexDigest(id) {
				return errors.New(recoveryArchiveRequired)
			}
			continue
		}
		return errors.New(recoveryUnknownArgument)
	}
	if op != "speaker.recovery_delete" {
		return nil
	}
	if _, ok := fields["archive_sha256"]; !ok {
		return errors.New(recoveryArchiveRequired)
	}
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
	return nil
}

func recoveryArchiveID(name string) (string, bool) {
	if len(name) != len(recoveryPrefix)+64+len(recoverySuffix) || !strings.HasPrefix(name, recoveryPrefix) || !strings.HasSuffix(name, recoverySuffix) {
		return "", false
	}
	id := name[len(recoveryPrefix) : len(recoveryPrefix)+64]
	return id, hexDigest(id)
}

func recoveryHolds(prefix []byte) string {
	switch {
	case bytes.HasPrefix(prefix, []byte(`{"enrollment_sha256":`)):
		return "enrolled_speakers_and_captures"
	case bytes.HasPrefix(prefix, []byte(`{"speaker_registry_sha256":`)):
		return "speaker_registry"
	}
	return "unrecognized"
}

func readPrefix(ctx context.Context, session privateStorageCaller, path string, n int) ([]byte, bool, error) {
	raw, err := session.HostCallTo(ctx, "fs.read", map[string]any{"root": "private", "path": path}, map[string]any{"offset": 0, "length": n})
	if err != nil {
		return nil, false, err
	}
	value, absent, err := hostRecordingResult(raw, path)
	if err != nil {
		return nil, false, err
	}
	if absent != "" {
		return nil, false, nil
	}
	var page struct {
		Size   *int64 `json:"size"`
		Offset *int64 `json:"offset"`
		Bytes  *int64 `json:"bytes"`
		Data   string `json:"data_b64"`
	}
	if json.Unmarshal(value, &page) != nil || page.Size == nil || page.Offset == nil || page.Bytes == nil || *page.Offset != 0 || *page.Bytes < 0 || *page.Bytes > int64(n) || *page.Bytes > *page.Size {
		return nil, false, errors.New("private recovery archive read invalid")
	}
	data, err := base64.StdEncoding.DecodeString(page.Data)
	if err != nil || int64(len(data)) != *page.Bytes || (*page.Size > 0 && *page.Bytes == 0) {
		return nil, false, errors.New("private recovery archive read incomplete")
	}
	return data, true, nil
}

func recoveryArchiveCall(ctx context.Context, session privateStorageCaller, op string, args aiiosdk.Object) (any, error) {
	if op == "speaker.recovery_list" {
		rows, truncated, err := privateInventory(ctx, session, recoveryDirectory)
		if err != nil {
			return nil, err
		}
		items := make([]map[string]any, 0)
		for _, row := range rows {
			id, ok := recoveryArchiveID(row.Name)
			if !ok || row.Dir || row.Symlink || row.Size == nil || *row.Size <= 0 || *row.Size > 12<<20 {
				continue
			}
			path := recoveryDirectory + "/" + row.Name
			prefix, found, err := readPrefix(ctx, session, path, 32)
			if err != nil {
				return nil, err
			}
			if !found {
				continue
			}
			item := map[string]any{"archive_sha256": id, "bytes": *row.Size, "holds": recoveryHolds(prefix)}
			if stamp, err := time.Parse(time.RFC3339, row.Modified); err == nil {
				item["saved_at"] = stamp.UTC().Format(time.RFC3339)
			}
			items = append(items, item)
		}
		return map[string]any{"status": "succeeded", "operation_result": map[string]any{"archives": items, "truncated": truncated}}, nil
	}
	if op != "speaker.recovery_delete" {
		return nil, errors.New("unknown recovery archive operation")
	}
	var request struct {
		ID string `json:"archive_sha256"`
	}
	if json.Unmarshal(args, &request) != nil || !hexDigest(request.ID) {
		return nil, errors.New(recoveryArchiveRequired)
	}
	path := recoveryDirectory + "/" + recoveryPrefix + request.ID + recoverySuffix

	if _, found, err := readPrefix(ctx, session, path, 1); err != nil {
		return nil, err
	} else if !found {
		return map[string]any{"status": "succeeded", "operation_result": map[string]any{"archive_sha256": request.ID, "deleted": false}}, nil
	}
	raw, err := session.HostCallTo(ctx, "fs.delete", map[string]any{"root": "private", "path": path}, map[string]any{})
	if err != nil {
		return nil, err
	}
	value, _, err := hostRecordingResult(raw, path)
	if err != nil {
		return nil, err
	}
	var deletion struct {
		Deleted *bool `json:"deleted"`
	}
	if json.Unmarshal(value, &deletion) != nil || deletion.Deleted == nil {
		return nil, errors.New("private recovery archive deletion receipt missing")
	}
	return map[string]any{"status": "succeeded", "operation_result": map[string]any{"archive_sha256": request.ID, "deleted": *deletion.Deleted}}, nil
}
