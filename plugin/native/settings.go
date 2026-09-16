package main

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// Private worker composition only. No public operation, persistence or
// write-capability is added: the sole upstream method is settings.get.
type settingsQuery struct {
	ID        uint64 `json:"id"`
	SessionID string `json:"session_id"`
}
type settingsReply struct {
	settingsQuery
	Values json.RawMessage `json:"values,omitempty"`
	Error  string          `json:"error,omitempty"`
}

func (q settingsQuery) valid() bool { return q.ID > 0 && q.SessionID != "" && len(q.SessionID) <= 128 }

func settingsValues(raw []byte) (json.RawMessage, error) {
	if len(raw) > 32768 {
		return nil, errors.New("host settings exceed bound")
	}
	var reply struct {
		Status  string `json:"status"`
		Success *bool  `json:"success"`
		OK      *bool  `json:"ok"`
		Result  struct {
			Values json.RawMessage `json:"values"`
		} `json:"operation_result"`
	}
	if err := json.Unmarshal(raw, &reply); err != nil {
		return nil, errors.New("malformed host settings reply")
	}
	if reply.Status != "succeeded" || (reply.Success != nil && !*reply.Success) || (reply.OK != nil && !*reply.OK) {
		return nil, errors.New("host settings read did not succeed")
	}
	var values map[string]json.RawMessage
	if err := json.Unmarshal(reply.Result.Values, &values); err != nil || values == nil {
		return nil, errors.New("host settings values must be an object")
	}
	return reply.Result.Values, nil
}

func (c *carrier) startSettings() {
	c.workers.Add(1)
	go func() {
		defer c.workers.Done()
		for {
			select {
			case <-c.stop:
				return
			case q := <-c.settings:
				c.mu.Lock()
				s := c.session
				c.mu.Unlock()
				c.readSettings(q, func(ctx context.Context) (aiiosdk.Object, error) {
					if s == nil {
						return nil, errors.New("settings requested before session admission")
					}
					return s.HostCall(ctx, "settings.get", map[string]any{})
				})
			}
		}
	}()
}

func (c *carrier) readSettings(q settingsQuery, get func(context.Context) (aiiosdk.Object, error)) {
	// Neither the private reply reader nor SDK admission waits for the host.
	// The worker has a two-second preparation bound; this retires before it.
	ctx, cancel := context.WithTimeout(c.ctx, 1500*time.Millisecond)
	defer cancel()
	result, err := get(ctx)
	reply := &settingsReply{settingsQuery: q}
	if err == nil {
		reply.Values, err = settingsValues(result)
	}
	if err != nil {
		// No raw host payload/error is echoed: settings may contain private text.
		reply.Error = "host settings unavailable or invalid"
	}
	select {
	case <-c.stop:
		return
	default:
	}
	select {
	case c.writes <- privateRequest{Settings: reply}:
	case <-c.stop:
	default:
		c.fail(errors.New("private settings reply queue full"))
	}
}
