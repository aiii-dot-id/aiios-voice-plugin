package main

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// Private worker composition only. No public operation, persistence or
// write-capability is added: the sole upstream method is settings.get.
type settingsQuery struct {
	ID        uint64 `json:"id"`
	SessionID string `json:"session_id"`
}

// settingsRequest is the worker's question. refresh marks one an open
// session asks as a reply begins: the settings alone, inside a read's time,
// with no correction list read for it (the list is a session's, taken at its
// opening).
type settingsRequest struct {
	settingsQuery
	Refresh bool `json:"refresh,omitempty"`
}

type settingsReply struct {
	settingsQuery
	Values json.RawMessage `json:"values,omitempty"`
	// Corrections is the stored correction list's exact bytes, present only
	// when a list is stored and the store yielded it (vocabulary.go).
	Corrections json.RawMessage `json:"corrections,omitempty"`
	Error       string          `json:"error,omitempty"`
	// Reason says which of three things an Error was, for a refusal that
	// names it: the host gave no answer in time (settingsNoAnswer), the
	// host answered an error (settingsHostError), or what it answered is
	// not settings (settingsNotSettings).
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

// settingsLane is the worker's newest request for a session's settings and
// the read now in flight for one. The worker answers only its newest request
// (worker.cpp settings: an earlier id is passed over), so only the newest is
// worth the host's time.
//
// It was a channel of one, read in order: the read of a session that had
// been aborted went on to its limit, the next open's read waited behind it
// and the worker gave that open up; a third request in the same wait found
// the channel full, and that failed the carrier. The opening's wait is now
// the host's time for a write and not a second and a half, so a read left
// running behind an abort would hold the next open far longer.
type settingsLane struct {
	mu     sync.Mutex
	next   *settingsRequest   // asked and not begun; a later ask replaces it
	retire context.CancelFunc // ends the read in flight, when there is one
	wake   chan struct{}      // capacity one: there is something to take
}

// offer makes q the request to read: it replaces one that had not begun and
// retires the read in flight, whose answer the worker would pass over.
func (l *settingsLane) offer(q settingsRequest) {
	l.mu.Lock()
	l.next = &q
	if l.retire != nil {
		l.retire()
	}
	l.mu.Unlock()
	select {
	case l.wake <- struct{}{}:
	default: // a wake is already waiting, and it finds this request
	}
}

// take is the request to read now and the context its read lives in, which
// a later offer ends.
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

// finished releases the context of the read that has ended.
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

// serveSettings reads the settings for the worker's newest request, one
// read at a time, until the carrier stops.
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
				// A reply is waiting a moment for this (the worker's
				// reply_settings); the answer, late or not, also serves
				// the reply after, so it is given a read's whole time.
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

// readSettingsWith also hands the session its correction list, read inside
// the same bound as the settings: a store that does not answer in what is
// left of it costs the session its corrections, never its open.
func (c *carrier) readSettingsWith(q settingsQuery, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	c.readSettingsIn(c.ctx, q, get, corrections)
}

// readSettingsIn reads inside the life of one request: when parent ends and
// the carrier has not, the worker has asked again, and this read says
// nothing, because the worker would pass its answer over.
func (c *carrier) readSettingsIn(parent context.Context, q settingsQuery, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	// Neither the private reply reader nor SDK admission waits for the host.
	// The worker waits a margin longer for this (limits.workerOpening), so
	// this always retires first. The bound was 1.5 s, typed here; on a disk
	// that was slow for a moment a session was refused at its opening.
	c.readSettingsBounded(parent, q, c.limits.opening(), get, corrections)
}

func (c *carrier) readSettingsBounded(parent context.Context, q settingsQuery, bound time.Duration, get func(context.Context) (aiiosdk.Object, error), corrections func(context.Context) json.RawMessage) {
	ctx, cancel := context.WithTimeout(parent, bound)
	defer cancel()
	result, err := get(ctx)
	if parent.Err() != nil && c.ctx.Err() == nil {
		return // retired for a newer request
	}
	reply := &settingsReply{settingsQuery: q}
	if err != nil {
		reply.Reason = settingsHostError
		if ctx.Err() != nil {
			reply.Reason = settingsNoAnswer
		}
	} else if reply.Values, err = settingsValues(result); err != nil {
		reply.Reason = settingsNotSettings
	}
	if err == nil && corrections != nil {
		reply.Corrections = corrections(ctx)
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
