package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

func privateFixture(t *testing.T) (*carrier, *io.PipeReader, *io.PipeWriter) {
	t.Helper()
	requests, in := io.Pipe()
	out, replies := io.Pipe()
	c := newCarrier(in, out, func() {})
	c.startPrivate()
	go c.read()
	t.Cleanup(func() { c.fail(io.EOF); replies.Close(); requests.Close(); c.workers.Wait(); <-c.readDone })
	return c, requests, replies
}

type workerAnswer struct {
	Value any
	Err   error
}

func pendingResult(t *testing.T, c *carrier, op string) <-chan workerAnswer {
	t.Helper()
	result := make(chan workerAnswer, 1)
	err := c.enqueue(nil, op, aiiosdk.Object(`{}`), func(value any, err error) { result <- workerAnswer{value, err} })
	if err != nil {
		t.Fatal(err)
	}
	select {
	case <-result:
		t.Fatal("queueing fabricated immediate admission")
	default:
	}
	return result
}
func awaitResult(t *testing.T, p <-chan workerAnswer) workerAnswer {
	t.Helper()
	select {
	case r := <-p:
		return r
	case <-time.After(time.Second):
		t.Fatal("missing result")
		return workerAnswer{}
	}
}

func TestPrivateOrderedEnqueueOutOfOrderRepliesAndRefusal(t *testing.T) {
	c, requests, replies := privateFixture(t)
	first := pendingResult(t, c, "synthesize")
	second := pendingResult(t, c, "stop_playback")
	scanner := bufio.NewScanner(requests)
	for i, want := range []string{"synthesize", "stop_playback"} {
		if !scanner.Scan() {
			t.Fatal("missing ordered write")
		}
		var req privateRequest
		if err := json.Unmarshal(scanner.Bytes(), &req); err != nil {
			t.Fatal(err)
		}
		if req.ID != uint64(i+1) || req.Operation != want {
			t.Fatalf("reordered %+v", req)
		}
	}
	if _, err := io.WriteString(replies, "{\"id\":2,\"result\":{\"fenced\":true}}\n"); err != nil {
		t.Fatal(err)
	}
	if r := awaitResult(t, second); r.Err != nil || string(r.Value.(json.RawMessage)) != `{"fenced":true}` {
		t.Fatalf("stop: %+v", r)
	}
	select {
	case <-first:
		t.Fatal("synthesis outcome invented from stop")
	default:
	}
	if _, err := io.WriteString(replies, "{\"id\":1,\"error\":\"STALE_SYNTHESIS\"}\n"); err != nil {
		t.Fatal(err)
	}
	if r := awaitResult(t, first); r.Err == nil || r.Err.Error() != "STALE_SYNTHESIS" {
		t.Fatalf("lost refusal: %+v", r)
	}
}

func TestPrivateFailedTransportReleasesEveryPendingResult(t *testing.T) {
	c, _, _ := privateFixture(t)
	a := pendingResult(t, c, "synthesize")
	b := pendingResult(t, c, "stop_playback")
	c.fail(errors.New("injected broken pipe"))
	for _, p := range []<-chan workerAnswer{a, b} {
		if r := awaitResult(t, p); r.Err == nil || !strings.Contains(r.Err.Error(), "unknown") {
			t.Fatalf("false known outcome: %+v", r)
		}
	}
	if err := c.enqueue(nil, "status", aiiosdk.Object(`{}`), nil); err == nil {
		t.Fatal("admitted after failure")
	}
}

func TestPrivateDeadlineCoversBlockedWrite(t *testing.T) {
	c, _, _ := privateFixture(t)
	p := pendingResult(t, c, "synthesize")
	c.mu.Lock()
	c.pending[1].deadline = time.Now().Add(-time.Second)
	c.mu.Unlock()
	if r := awaitResult(t, p); r.Err == nil || !strings.Contains(r.Err.Error(), "timeout") {
		t.Fatalf("blocked write ignored deadline: %+v", r)
	}
	select {
	case <-c.stop:
	case <-time.After(time.Second):
		t.Fatal("transport did not stop")
	}
}

func TestPrivateUnknownReplyFaultsInsteadOfSatisfyingAnotherCall(t *testing.T) {
	c, _, replies := privateFixture(t)
	p := pendingResult(t, c, "synthesize")
	if _, err := io.WriteString(replies, "{\"id\":999,\"result\":{}}\n"); err != nil {
		t.Fatal(err)
	}
	if r := awaitResult(t, p); r.Err == nil || !strings.Contains(r.Err.Error(), "unsolicited") {
		t.Fatalf("unknown id accepted: %+v", r)
	}
}

func TestPrivateCapacityRefusesWithoutAdditionalEnqueue(t *testing.T) {
	c, _, _ := privateFixture(t)
	for range 64 {
		pendingResult(t, c, "status")
	}
	if err := c.enqueue(nil, "status", aiiosdk.Object(`{}`), nil); err == nil {
		t.Fatal("unbounded pending admissions")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.pending) != 64 || c.id != 64 {
		t.Fatal("overflow allocated another request")
	}
}
