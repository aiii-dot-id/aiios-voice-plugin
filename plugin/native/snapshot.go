package main

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
	"time"
)

// Fixed, host-owned profile and explicitly requested pending evidence; never a
// worker-selected path. Pending captures are not enrolled speaker identity.
// Missing, refused, corrupt and unreadable are ALL unavailable. Only an actual
// canonical empty snapshot means that no speakers have been enrolled.
const snapshotPath = "uid/enrollment.json"
const pendingCapturesPath = "uid/captures.json"
const snapshotPageBytes = 65536

type snapshotQuery struct {
	settingsQuery
	Resource string `json:"resource,omitempty"`
	Offset   uint64 `json:"offset"`
	Digest   bool   `json:"digest"`
	Action   string `json:"action,omitempty"`
	Upload   string `json:"upload,omitempty"`
	Data     string `json:"data_b64,omitempty"`
	Append   bool   `json:"append,omitempty"`
	SHA      string `json:"sha256,omitempty"`
	Expected string `json:"expected_sha256,omitempty"`
	Absent   bool   `json:"expected_absent,omitempty"`
}

func hexDigest(s string) bool {
	b, e := hex.DecodeString(s)
	return e == nil && len(b) == 32 && hex.EncodeToString(b) == s
}
func (q snapshotQuery) valid() bool {
	if !q.settingsQuery.valid() || (q.Resource != "" && q.Resource != "captures") || q.Offset > q.limit() {
		return false
	}
	switch q.Action {
	case "":
		return q.Upload == "" && q.Data == "" && !q.Append && q.SHA == "" && q.Expected == "" && !q.Absent
	case "stage":
		data, e := base64.StdEncoding.Strict().DecodeString(q.Data)
		return hexDigest(q.Upload) && e == nil && len(data) > 0 && len(data) <= snapshotPageBytes && base64.StdEncoding.EncodeToString(data) == q.Data && q.Offset == 0 && !q.Digest && q.SHA == "" && q.Expected == "" && !q.Absent
	case "publish":
		return hexDigest(q.Upload) && hexDigest(q.SHA) && ((q.Absent && q.Expected == "") || (!q.Absent && hexDigest(q.Expected))) && q.Data == "" && !q.Append && q.Offset == 0 && !q.Digest
	}
	return false
}
func (q snapshotQuery) limit() uint64 {
	if q.Resource == "captures" {
		return snapshotPageBytes
	}
	return 8 << 20
}
func (q snapshotQuery) stagePath() string {
	if q.Resource == "captures" {
		return "uid/.captures-" + q.Upload + ".pending"
	}
	return "uid/.enrollment-" + q.Upload + ".pending"
}
func (q snapshotQuery) target() string {
	if q.Action == "stage" {
		return q.stagePath()
	}
	if q.Resource == "captures" {
		return pendingCapturesPath
	}
	return snapshotPath
}
func (q snapshotQuery) call() (string, map[string]any) {
	switch q.Action {
	case "stage":
		return "fs.write", map[string]any{"data_b64": q.Data, "append": q.Append}
	case "publish":
		a := map[string]any{"from": q.stagePath(), "sha256": q.SHA}
		if q.Absent {
			a["expected_absent"] = true
		} else {
			a["expected_sha256"] = q.Expected
		}
		return "fs.publish", a
	default:
		return "fs.read", map[string]any{"offset": q.Offset, "length": snapshotPageBytes, "digest": q.Digest}
	}
}

type snapshotReply struct {
	settingsQuery
	Value  json.RawMessage `json:"value,omitempty"`
	Error  string          `json:"error,omitempty"`
	Reason string          `json:"reason_code,omitempty"`
}

type snapshotFailure struct{ reason string }

func (e snapshotFailure) Error() string { return "host UID storage unavailable: " + e.reason }

func snapshotValue(raw []byte, q snapshotQuery) (json.RawMessage, error) {
	bad := errors.New("host snapshot page unavailable or invalid")
	if len(raw) > 128<<10 || !q.valid() {
		return nil, bad
	}
	var reply struct {
		Status  string          `json:"status"`
		Success *bool           `json:"success"`
		OK      *bool           `json:"ok"`
		Result  json.RawMessage `json:"operation_result"`
		Reason  string          `json:"reasonCode"`
		Snake   string          `json:"reason_code"`
	}
	if json.Unmarshal(raw, &reply) != nil {
		return nil, bad
	}
	if reply.Status != "succeeded" || (reply.Success != nil && !*reply.Success) || (reply.OK != nil && !*reply.OK) {
		if reply.Reason != "" && reply.Snake != "" && reply.Reason != reply.Snake {
			return nil, bad
		}
		code := reply.Reason
		if code == "" {
			code = reply.Snake
		}
		// Only a typed, failed first-page read may mean absent. No authorization,
		// timeout, malformed response, or later missing page creates an empty file.
		if q.Action == "" && q.Offset == 0 && reply.Status == "failed" && code == "FS_NOT_FOUND" {
			return nil, snapshotFailure{code}
		}
		if code == "FS_GENERATION_MISMATCH" || code == "FS_DIGEST_MISMATCH" {
			return nil, snapshotFailure{code}
		}
		return nil, bad
	}
	if q.Action != "" {
		var v struct {
			Root       string  `json:"root"`
			Path       string  `json:"path"`
			Bytes      *uint64 `json:"bytes"`
			Size       *uint64 `json:"size"`
			SHA        string  `json:"sha256"`
			Replaced   *bool   `json:"replaced"`
			Durable    *bool   `json:"durable"`
			Durability string  `json:"durability"`
		}
		if json.Unmarshal(reply.Result, &v) != nil || v.Root != "private" || v.Path != q.target() || v.Size == nil || *v.Size > q.limit() {
			return nil, bad
		}
		if q.Action == "stage" {
			data, _ := base64.StdEncoding.DecodeString(q.Data)
			if v.Bytes == nil || *v.Bytes != uint64(len(data)) {
				return nil, bad
			}
		} else if v.SHA != q.SHA || v.Replaced == nil || *v.Replaced == q.Absent || v.Durable == nil || v.Durability == "" {
			return nil, bad
		}
		return reply.Result, nil
	}
	var v struct {
		Root   string  `json:"root"`
		Path   string  `json:"path"`
		Data   string  `json:"data_b64"`
		Bytes  *uint64 `json:"bytes"`
		Offset *uint64 `json:"offset"`
		Size   *uint64 `json:"size"`
		EOF    *bool   `json:"eof"`
		SHA    string  `json:"sha256"`
	}
	if json.Unmarshal(reply.Result, &v) != nil || v.Root != "private" || v.Path != q.target() || v.Bytes == nil || v.Offset == nil || v.Size == nil || v.EOF == nil {
		return nil, bad
	}
	data, err := base64.StdEncoding.Strict().DecodeString(v.Data)
	if err != nil || base64.StdEncoding.EncodeToString(data) != v.Data || len(data) > snapshotPageBytes || uint64(len(data)) != *v.Bytes || *v.Offset != q.Offset || *v.Size > q.limit() || *v.Offset > *v.Size || *v.Bytes > *v.Size-*v.Offset || *v.EOF != (*v.Bytes+*v.Offset == *v.Size) {
		return nil, bad
	}
	if q.Digest {
		if len(v.SHA) != 64 {
			return nil, bad
		}
		for _, c := range v.SHA {
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
				return nil, bad
			}
		}
	}
	return reply.Result, nil
}
func (c *carrier) startSnapshots() {
	c.workers.Add(1)
	go func() {
		defer c.workers.Done()
		for {
			select {
			case <-c.stop:
				return
			case q := <-c.snapshots:
				c.mu.Lock()
				s := c.session
				c.mu.Unlock()
				c.readSnapshot(q, func(ctx context.Context) (aiiosdk.Object, error) {
					if s == nil {
						return nil, errors.New("snapshot before session admission")
					}
					op, args := q.call()
					return s.HostCallTo(ctx, op, map[string]any{"root": "private", "path": q.target()}, args)
				})
			}
		}
	}()
}
func (c *carrier) readSnapshot(q snapshotQuery, get func(context.Context) (aiiosdk.Object, error)) {
	ctx, cancel := context.WithTimeout(c.ctx, 1500*time.Millisecond)
	defer cancel()
	raw, err := get(ctx)
	reply := &snapshotReply{settingsQuery: q.settingsQuery}
	if err == nil {
		reply.Value, err = snapshotValue(raw, q)
	}
	if err != nil {
		reply.Error = "host UID snapshot unavailable or invalid"
		var classified snapshotFailure
		if errors.As(err, &classified) {
			reply.Reason = classified.reason
		}
	}
	select {
	case <-c.stop:
		return
	default:
	}
	select {
	case c.writes <- privateRequest{Snapshot: reply}:
	case <-c.stop:
	default:
		c.fail(errors.New("private snapshot reply queue full"))
	}
}
