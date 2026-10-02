package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// Recording inventory and removal are plugin-specific broker acts. The SDK
// only supplies generic hostcalls; no speech or filesystem special case is
// added to its public surface. Paths are derived exclusively from a canonical
// digest in the host's own private recordings directory.
type recordingEntry struct {
	Name     string `json:"name"`
	Dir      bool   `json:"dir"`
	Symlink  bool   `json:"symlink"`
	Size     *int64 `json:"size"`
	Modified string `json:"modified"`
}

type privateStorageCaller interface {
	HostCallTo(context.Context, string, any, any) (aiiosdk.Object, error)
}

func hostRecordingResult(raw aiiosdk.Object, path string) (json.RawMessage, string, error) {
	var reply struct {
		Status  string          `json:"status"`
		Success *bool           `json:"success"`
		OK      *bool           `json:"ok"`
		Reason  string          `json:"reasonCode"`
		Snake   string          `json:"reason_code"`
		Result  json.RawMessage `json:"operation_result"`
	}
	if json.Unmarshal(raw, &reply) != nil || (reply.Reason != "" && reply.Snake != "" && reply.Reason != reply.Snake) {
		return nil, "", errors.New("invalid private recording broker reply")
	}
	code := reply.Reason
	if code == "" {
		code = reply.Snake
	}
	if reply.Status == "failed" && code == "FS_NOT_FOUND" {
		return nil, code, nil
	}
	if reply.Status != "succeeded" {
		return nil, code, fmt.Errorf("private recording broker refused: %s", code)
	}
	if (reply.Success != nil && !*reply.Success) || (reply.OK != nil && !*reply.OK) {
		return nil, "", errors.New("private recording broker contradicted successful status")
	}
	var target struct {
		Root string `json:"root"`
		Path string `json:"path"`
	}
	if json.Unmarshal(reply.Result, &target) != nil || target.Root != "private" || target.Path != path {
		return nil, "", errors.New("private recording broker target differs")
	}
	return reply.Result, "", nil
}

func recordingInventory(value json.RawMessage) ([]recordingEntry, bool, error) {
	var listing struct {
		Entries   []recordingEntry `json:"entries"`
		Truncated bool             `json:"truncated"`
	}
	if json.Unmarshal(value, &listing) != nil || len(listing.Entries) > 1024 || listing.Entries == nil {
		return nil, false, errors.New("private recording inventory invalid")
	}
	return listing.Entries, listing.Truncated, nil
}

// A pending upload belongs to the voice plugin only when its name exactly
// matches one of the snapshot bridge's stage paths. Host mtime is checked on
// the same machine. Two minutes exceeds the bridge's 30-second publication
// deadline and keeps a concurrent pinned predecessor's active stage intact.
func expiredVoiceStage(directory string, row recordingEntry, now time.Time) bool {
	if row.Dir || row.Symlink || row.Size == nil || *row.Size < 0 || row.Modified == "" {
		return false
	}
	stamp, err := time.Parse(time.RFC3339, row.Modified)
	if err != nil || now.Sub(stamp) < 2*time.Minute {
		return false
	}
	if directory == "recordings" {
		return len(row.Name) == 1+64+len(".pending") && strings.HasPrefix(row.Name, ".") &&
			strings.HasSuffix(row.Name, ".pending") && hexDigest(row.Name[1:65]) && *row.Size <= 1<<20
	}
	if directory == "uid" {
		for _, prefix := range []string{".speakers-", ".enrollment-", ".captures-", ".recovery-"} {
			if len(row.Name) == len(prefix)+64+len(".pending") && strings.HasPrefix(row.Name, prefix) &&
				strings.HasSuffix(row.Name, ".pending") && hexDigest(row.Name[len(prefix):len(prefix)+64]) && *row.Size <= 12<<20 {
				return true
			}
		}
	}
	return false
}

func privateInventory(ctx context.Context, session privateStorageCaller, directory string) ([]recordingEntry, bool, error) {
	raw, err := session.HostCallTo(ctx, "fs.list", map[string]any{"root": "private", "path": directory}, map[string]any{})
	if err != nil {
		return nil, false, err
	}
	value, absent, err := hostRecordingResult(raw, directory)
	if err != nil {
		return nil, false, err
	}
	if absent != "" {
		return []recordingEntry{}, false, nil
	}
	return recordingInventory(value)
}

func pruneVoiceStages(ctx context.Context, session privateStorageCaller, now time.Time) (any, error) {
	deleted, bytesFreed := 0, int64(0)
	truncated := false
	for _, directory := range []string{"recordings", "uid"} {
		rows, more, err := privateInventory(ctx, session, directory)
		if err != nil {
			return nil, err
		}
		truncated = truncated || more
		for _, row := range rows {
			if !expiredVoiceStage(directory, row, now) {
				continue
			}
			path := directory + "/" + row.Name
			raw, err := session.HostCallTo(ctx, "fs.delete", map[string]any{"root": "private", "path": path}, map[string]any{})
			if err != nil {
				return nil, err
			}
			value, _, err := hostRecordingResult(raw, path)
			if err != nil {
				return nil, err
			}
			var receipt struct {
				Deleted *bool `json:"deleted"`
			}
			if json.Unmarshal(value, &receipt) != nil || receipt.Deleted == nil {
				return nil, errors.New("private stage deletion receipt missing")
			}
			if *receipt.Deleted {
				deleted++
				bytesFreed += *row.Size
			}
		}
	}
	return map[string]any{"status": "succeeded", "operation_result": map[string]any{"deleted": deleted, "listed_bytes_freed": bytesFreed, "truncated": truncated}}, nil
}

func (c *carrier) recordingStore(control *aiiosdk.Control) {
	c.mu.Lock()
	select {
	case <-c.stop:
		c.mu.Unlock()
		control.Answer(nil, errors.New("worker lane ended"))
		return
	default:
	}
	c.session = control.Session
	c.workers.Add(1)
	c.mu.Unlock()
	go func() {
		defer c.workers.Done()
		ctx, cancel := context.WithTimeout(c.ctx, 10*time.Second)
		defer cancel()
		result, err := c.recordingStoreCall(ctx, control.Session, control.Op, control.Args)
		control.Answer(result, err)
	}()
}

func (c *carrier) recordingStoreCall(ctx context.Context, session privateStorageCaller, op string, args aiiosdk.Object) (any, error) {
	if op == "recording.prune" {
		return pruneVoiceStages(ctx, session, time.Now().UTC())
	}
	const directory = "recordings"
	if op == "recording.list" {
		rows, truncated, err := privateInventory(ctx, session, directory)
		if err != nil {
			return nil, err
		}
		items := make([]map[string]any, 0)
		for _, row := range rows {
			id := strings.TrimSuffix(row.Name, ".wav")
			if row.Dir || row.Symlink || !strings.HasSuffix(row.Name, ".wav") || !hexDigest(id) || row.Size == nil || *row.Size < 44 || *row.Size > 1<<20 {
				continue
			}
			items = append(items, map[string]any{"recording_id": id, "bytes": *row.Size})
		}
		return map[string]any{"status": "succeeded", "operation_result": map[string]any{"recordings": items, "truncated": truncated}}, nil
	}
	if op != "recording.delete" {
		return nil, errors.New("unknown recording store operation")
	}
	var request struct {
		ID string `json:"recording_id"`
	}
	if json.Unmarshal(args, &request) != nil || !hexDigest(request.ID) {
		return nil, errors.New("canonical recording_id required")
	}
	path := directory + "/" + request.ID + ".wav"
	// fs.read checks the exact target is regular. One private byte is enough;
	// it is never returned to the caller. fs.delete independently refuses links.
	// Recovery must not require enumerating an already overfull directory.
	raw, err := session.HostCallTo(ctx, "fs.read", map[string]any{"root": "private", "path": path}, map[string]any{"offset": 0, "length": 1})
	if err != nil {
		return nil, err
	}
	value, absent, err := hostRecordingResult(raw, path)
	if err != nil {
		return nil, err
	}
	found := absent == ""
	if found {
		var probe struct {
			Size   *int64 `json:"size"`
			Offset *int64 `json:"offset"`
			Bytes  *int64 `json:"bytes"`
			Data   string `json:"data_b64"`
		}
		if json.Unmarshal(value, &probe) != nil || probe.Size == nil || probe.Offset == nil || probe.Bytes == nil || *probe.Size < 0 || *probe.Offset != 0 || *probe.Bytes < 0 || *probe.Bytes > 1 || *probe.Bytes > *probe.Size {
			return nil, errors.New("private recording target probe invalid")
		}
		data, err := base64.StdEncoding.DecodeString(probe.Data)
		if err != nil || int64(len(data)) != *probe.Bytes || (*probe.Size > 0 && *probe.Bytes != 1) {
			return nil, errors.New("private recording target probe incomplete")
		}
	}
	if !found {
		return map[string]any{"status": "succeeded", "operation_result": map[string]any{"recording_id": request.ID, "deleted": false}}, nil
	}
	raw, err = session.HostCallTo(ctx, "fs.delete", map[string]any{"root": "private", "path": path}, map[string]any{})
	if err != nil {
		return nil, err
	}
	value, _, err = hostRecordingResult(raw, path)
	if err != nil {
		return nil, err
	}
	var deletion struct {
		Deleted *bool `json:"deleted"`
	}
	if json.Unmarshal(value, &deletion) != nil || deletion.Deleted == nil {
		return nil, errors.New("private recording deletion receipt missing")
	}
	return map[string]any{"status": "succeeded", "operation_result": map[string]any{"recording_id": request.ID, "deleted": *deletion.Deleted}}, nil
}
