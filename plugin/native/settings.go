package main

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

type settingsQuery struct {
	ID        uint64 `json:"id"`
	SessionID string `json:"session_id"`
}

type settingsRequest struct {
	settingsQuery
	Refresh bool `json:"refresh,omitempty"`
}

type settingsReply struct {
	settingsQuery
	Values json.RawMessage `json:"values,omitempty"`

	Corrections json.RawMessage `json:"corrections,omitempty"`
	Error       string          `json:"error,omitempty"`

	Reason string `json:"reason_code,omitempty"`
}

const (
	settingsNoAnswer    = "HOST_SETTINGS_NO_ANSWER"
	settingsHostError   = "HOST_SETTINGS_ERROR"
	settingsNotSettings = "HOST_SETTINGS_NOT_SETTINGS"
)

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

type settingsLane struct {
	mu     sync.Mutex
	next   *settingsRequest
	retire context.CancelFunc
	wake   chan struct{}
}

func (l *settingsLane) offer(q settingsRequest) {
	l.mu.Lock()
	l.next = &q
	if l.retire != nil {
		l.retire()
	}
	l.mu.Unlock()
	select {
	case l.wake <- struct{}{}:
	default:
	}
}

func (l *settingsLane) take(parent context.Context) (settingsRequest, context.Context, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.next == nil {
		return settingsRequest{}, nil, false
	}
	q := *l.next
	l.next = nil
	ctx, cancel := context.WithCancel(parent)
	l.retire = cancel
	return q, ctx, true
}

func (l *settingsLane) finished() {
	l.mu.Lock()
	if l.retire != nil {
		l.retire()
		l.retire = nil
	}
	l.mu.Unlock()
}

func (c *carrier) startSettings() {
	c.workers.Add(1)
	go func() {
		defer c.workers.Done()
		session := func() *aiiosdk.Session {
			c.mu.Lock()
			defer c.mu.Unlock()
			return c.session
		}
		c.serveSettings(func(ctx context.Context) (aiiosdk.Object, error) {
			s := session()
			if s == nil {
				return nil, errors.New("settings requested before session admission")
			}
			return s.HostCall(ctx, "settings.get", map[string]any{})
		}, func(ctx context.Context) json.RawMessage {
			s := session()
			if s == nil {
				return nil
			}
			return sessionCorrections(ctx, s)
		})
	}()
}

func (c *carrier) serveSettings(get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	for {
		select {
		case <-c.stop:
			return
		case <-c.settings.wake:
			q, ctx, ok := c.settings.take(c.ctx)
			if !ok {
				continue
			}
			if q.Refresh {

				c.readSettingsBounded(ctx, q.settingsQuery, c.limits.query(""), get, nil)
			} else {
				c.readSettingsIn(ctx, q.settingsQuery, get, corrections)
			}
			c.settings.finished()
		}
	}
}

func (c *carrier) readSettings(q settingsQuery, get func(context.Context) (aiiosdk.Object, error)) {
	c.readSettingsWith(q, get, nil)
}

func (c *carrier) readSettingsWith(q settingsQuery, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	c.readSettingsIn(c.ctx, q, get, corrections)
}

func (c *carrier) readSettingsIn(parent context.Context, q settingsQuery, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {

	c.readSettingsBounded(parent, q, c.limits.opening(), get, corrections)
}

func (c *carrier) readSettingsBounded(parent context.Context, q settingsQuery, bound time.Duration, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	ctx, cancel := context.WithTimeout(parent, bound)
	defer cancel()
	result, err := get(ctx)
	if parent.Err() != nil && c.ctx.Err() == nil {
		return
	}
	reply := &settingsReply{settingsQuery: q}
	if err != nil {
		reply.Reason = settingsHostError
		if ctx.Err() != nil {
			reply.Reason = settingsNoAnswer
		}
	} else if reply.Values, err = settingsValues(result); err != nil {
		reply.Reason = settingsNotSettings
	} else {
		c.keepRule(reply.Values)
	}
	if err == nil && corrections != nil {
		reply.Corrections = corrections(ctx)
	}
	if err != nil {

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
