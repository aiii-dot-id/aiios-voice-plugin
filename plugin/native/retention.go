package main

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

const retentionArgument = "_carrier_settings"

var retentionKeys = []string{"anonymous_profiles", "anonymous_expiry_days"}

func registryOperation(op string) bool {
	switch op {
	case "speaker.buckets", "speaker.associate", "speaker.forget", "speaker.link":
		return true
	}
	return false
}

func withRetention(args aiiosdk.Object, values json.RawMessage) (aiiosdk.Object, error) {
	var saved map[string]json.RawMessage
	if err := json.Unmarshal(values, &saved); err != nil || saved == nil {
		return nil, errors.New("host settings values must be an object")
	}
	rule := map[string]json.RawMessage{}
	for _, key := range retentionKeys {
		if value, ok := saved[key]; ok {
			rule[key] = value
		}
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(args, &fields); err != nil || fields == nil {
		return nil, errors.New("bounded enrollment arguments required")
	}
	encoded, err := json.Marshal(rule)
	if err != nil {
		return nil, err
	}
	fields[retentionArgument] = encoded
	return json.Marshal(fields)
}

func (c *carrier) registryControl(control *aiiosdk.Control) {
	c.mu.Lock()
	select {
	case <-c.stop:
		c.mu.Unlock()
		control.Answer(nil, errors.New("worker lane ended"))
		return
	default:
	}
	c.workers.Add(1)
	c.mu.Unlock()
	go func() {
		defer c.workers.Done()
		args := c.retentionFor(control.Args, func(ctx context.Context) (aiiosdk.Object, error) {
			return control.Session.HostCall(ctx, "settings.get", map[string]any{})
		})
		if err := c.enqueue(control.Session, control.Op, args, control.Answer); err != nil {
			control.Answer(nil, err)
		}
	}()
}

func (c *carrier) keepRule(values json.RawMessage) {
	rule, err := withRetention(aiiosdk.Object(`{}`), values)
	if err != nil {
		return
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(rule, &fields) != nil {
		return
	}
	c.mu.Lock()
	c.rule = fields[retentionArgument]
	c.mu.Unlock()
}

func (c *carrier) retentionFor(args aiiosdk.Object, get func(context.Context) (aiiosdk.Object, error)) aiiosdk.Object {
	if bound := c.limits.retentionRead(); bound > 0 {
		ctx, cancel := context.WithTimeout(c.ctx, bound)
		result, err := get(ctx)
		cancel()
		if err == nil {
			if values, err := settingsValues(result); err == nil {
				c.keepRule(values)
			}
		}
	}
	c.mu.Lock()
	rule := c.rule
	c.mu.Unlock()
	if rule == nil {
		return args
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(args, &fields) != nil || fields == nil {
		return args
	}
	fields[retentionArgument] = rule
	with, err := json.Marshal(fields)
	if err != nil {
		return args
	}
	return with
}
