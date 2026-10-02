package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"sync"
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

func awaitFault(t *testing.T, c *carrier, want string) {
	t.Helper()
	select {
	case e := <-c.fault:
		if !strings.Contains(e.Error(), want) {
			t.Fatalf("wrong fault: %v", e)
		}
	case <-time.After(time.Second):
		t.Fatalf("no %q fault", want)
	}
}

// fail answers before it records the fault, so after awaitFault an answer
// that has not arrived never will.
func unanswered(t *testing.T, p <-chan workerAnswer) {
	t.Helper()
	select {
	case r := <-p:
		t.Fatalf("a request the worker may have admitted was answered: %+v", r)
	default:
	}
}

// A failed transport divides pending requests by what the worker can have
// seen. One the writer took may have been admitted: it stays unanswered, and
// the lane's end reports it to the host as admission unknown, never as the
// error reply the host must read as a refusal. One still in the ordered queue
// never reached the worker, and saying so is a true refusal.
func TestPrivateFailedTransportRefusesOnlyRequestsNeverSent(t *testing.T) {
	c, requests, _ := privateFixture(t)
	taken := pendingResult(t, c, "synthesize")
	queued := pendingResult(t, c, "stop_playback")
	// One byte read proves the writer took the first request and is still
	// writing it, so the second cannot have left the queue.
	if _, err := io.ReadFull(requests, make([]byte, 1)); err != nil {
		t.Fatal(err)
	}
	c.fail(errors.New("injected broken pipe"))
	awaitFault(t, c, "injected broken pipe")
	if r := awaitResult(t, queued); r.Err == nil || !strings.Contains(r.Err.Error(), "not sent") {
		t.Fatalf("unsent request not refused as unsent: %+v", r)
	}
	unanswered(t, taken)
	if err := c.enqueue(nil, "status", aiiosdk.Object(`{}`), nil); err == nil {
		t.Fatal("admitted after failure")
	}
}

func TestPrivateDeadlineCoversBlockedWrite(t *testing.T) {
	c, requests, _ := privateFixture(t)
	p := pendingResult(t, c, "synthesize")
	if _, err := io.ReadFull(requests, make([]byte, 1)); err != nil {
		t.Fatal(err) // the write began and is blocked
	}
	c.mu.Lock()
	c.pending[1].deadline = time.Now().Add(-time.Second)
	c.mu.Unlock()
	awaitFault(t, c, "timeout")
	select {
	case <-c.stop:
	default:
		t.Fatal("transport did not stop")
	}
	unanswered(t, p) // part of it may have reached the worker
}

func TestPrivateUnknownReplyFaultsInsteadOfSatisfyingAnotherCall(t *testing.T) {
	c, requests, replies := privateFixture(t)
	p := pendingResult(t, c, "synthesize")
	if !bufio.NewScanner(requests).Scan() {
		t.Fatal("request not written")
	}
	if _, err := io.WriteString(replies, "{\"id\":999,\"result\":{}}\n"); err != nil {
		t.Fatal(err)
	}
	awaitFault(t, c, "unsolicited")
	unanswered(t, p)
}

// A SECOND READINESS IS A FAULT even after the first was taken: the channel's
// capacity refused a repeat only while the first announcement still sat in it,
// so a worker re-announcing after admission was silently forgotten.
func TestPrivateSecondReadinessIsAFaultAfterTheFirstWasTaken(t *testing.T) {
	c, _, replies := privateFixture(t)
	ready := "{\"ready\":{\"identity\":{\"backend\":\"deterministic-test-not-real-model\"}}}\n"
	if _, err := io.WriteString(replies, ready); err != nil {
		t.Fatal(err)
	}
	select {
	case <-c.ready:
	case <-time.After(time.Second):
		t.Fatal("first readiness not delivered")
	}
	if _, err := io.WriteString(replies, ready); err != nil {
		t.Fatal(err)
	}
	// fail() closes stop before it records the fault, so wait on the fault itself.
	select {
	case e := <-c.fault:
		if !strings.Contains(e.Error(), "duplicate") {
			t.Fatalf("wrong fault: %v", e)
		}
	case <-time.After(time.Second):
		t.Fatal("a second readiness after the first was taken did not fault the transport")
	}
	select {
	case <-c.stop:
	default:
		t.Fatal("the transport recorded a fault without stopping")
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

func TestRecordingPublicationGetsItsWorkerBudgetWithoutWideningSpeechControls(t *testing.T) {
	c, _, _ := privateFixture(t)
	pendingResult(t, c, "recording.record")
	pendingResult(t, c, "recording.status")
	c.mu.Lock()
	recording := c.pending[1].deadline
	status := c.pending[2].deadline
	c.mu.Unlock()
	if recording.Sub(status) < 40*time.Second {
		t.Fatalf("recording deadline did not cover bounded broker publication: recording=%v status=%v", recording, status)
	}
}

// heldEmitter is the public writer as the event owner sees it. It records
// each event and holds the first until release closes.
type heldEmitter struct {
	mu      sync.Mutex
	got     []string
	holding chan struct{}
	release chan struct{}
}

func (h *heldEmitter) emit(_ *aiiosdk.Session, event any) error {
	h.mu.Lock()
	h.got = append(h.got, string(event.(json.RawMessage)))
	first := len(h.got) == 1
	h.mu.Unlock()
	if first {
		close(h.holding)
		<-h.release
	}
	return nil
}

// finalEventsRead holds forwarding on the first of a worker's last events,
// then lets the worker exit: every event is read and the private lane has
// failed, with all but the first still queued behind the held forwarder.
func finalEventsRead(t *testing.T) (*carrier, *heldEmitter, chan struct{}, chan error, []string) {
	t.Helper()
	c, _, replies := privateFixture(t)
	c.mu.Lock()
	c.session = &aiiosdk.Session{} // the emitter stands in for its writer
	c.mu.Unlock()
	h := &heldEmitter{holding: make(chan struct{}), release: make(chan struct{})}
	t.Cleanup(func() {
		select {
		case <-h.release:
		default:
			close(h.release)
		}
	})
	settle, forwarded := make(chan struct{}), make(chan error, 1)
	go func() { forwarded <- c.forward(settle, h.emit) }()
	var want []string
	for i := range 32 {
		event := fmt.Sprintf(`{"type":"resources_released","sequence":%d}`, i)
		want = append(want, event)
		if _, err := fmt.Fprintf(replies, "{\"event\":%s}\n", event); err != nil {
			t.Fatal(err)
		}
	}
	<-h.holding
	_ = replies.Close() // the worker exits
	awaitFault(t, c, "EOF")
	<-c.readDone
	return c, h, settle, forwarded, want
}

func TestFinalWorkerEventsReachHealthyHostBeforeTeardown(t *testing.T) {
	c, h, settle, forwarded, want := finalEventsRead(t)
	flushed := -1 // events handed on when the flush began
	flush := func(*aiiosdk.Session, context.Context) error {
		h.mu.Lock()
		defer h.mu.Unlock()
		flushed = len(h.got)
		return nil
	}
	handed := make(chan error, 1)
	go func() { handed <- c.handoff(settle, forwarded, flush, 5*time.Second) }()
	<-settle         // teardown has begun
	close(h.release) // and the host takes events again
	select {
	case err := <-handed:
		if err != nil {
			t.Fatalf("healthy host: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("teardown did not return")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if !slices.Equal(h.got, want) {
		t.Fatalf("teardown abandoned events already read: forwarded %d of %d", len(h.got), len(want))
	}
	if flushed != len(want) {
		t.Fatalf("teardown flushed after %d of %d events were handed on", flushed, len(want))
	}
}

// Handed on is not written. When the flush cannot prove the final events
// written — the lane ended, or the writer did not catch up within the bound —
// teardown still ends within that bound and says delivery is unproven.
func TestFinalWorkerEventsUnflushedAreUnproven(t *testing.T) {
	for _, tc := range []struct {
		name  string
		flush func(*aiiosdk.Session, context.Context) error
		cause error
	}{
		{"lane ended", func(*aiiosdk.Session, context.Context) error {
			return fmt.Errorf("%w: 32 accepted frame(s) not confirmed written", aiiosdk.ErrLaneEnded)
		}, aiiosdk.ErrLaneEnded},
		{"writer behind", func(_ *aiiosdk.Session, ctx context.Context) error {
			<-ctx.Done()
			return fmt.Errorf("aiiosdk: flush outcome unknown: %w", ctx.Err())
		}, context.DeadlineExceeded},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, h, settle, forwarded, _ := finalEventsRead(t)
			close(h.release)
			began := time.Now()
			err := c.handoff(settle, forwarded, tc.flush, 100*time.Millisecond)
			if elapsed := time.Since(began); elapsed > 5*time.Second {
				t.Fatalf("teardown waited %v for an unconfirmed flush", elapsed)
			}
			if err == nil || !strings.Contains(err.Error(), "unproven") || !errors.Is(err, tc.cause) {
				t.Fatalf("unconfirmed final events were not reported: %v", err)
			}
		})
	}
}

func TestFinalWorkerEventsToStalledHostEndBoundedAndUnproven(t *testing.T) {
	c, _, settle, forwarded, _ := finalEventsRead(t)
	began := time.Now()
	err := c.handoff(settle, forwarded, func(*aiiosdk.Session, context.Context) error {
		t.Error("flushed events that were never handed on")
		return nil
	}, 100*time.Millisecond)
	if elapsed := time.Since(began); elapsed > 5*time.Second {
		t.Fatalf("teardown waited %v on a stalled host", elapsed)
	}
	if err == nil || !strings.Contains(err.Error(), "unproven") {
		t.Fatalf("undelivered final events were not reported: %v", err)
	}
}

// A full event queue drops worker events. Teardown counts them and says
// delivery is unproven, even when the flush proves written all it was given.
func TestDroppedFinalEventsMakeDeliveryUnproven(t *testing.T) {
	c, _, replies := privateFixture(t)
	c.mu.Lock()
	c.session = &aiiosdk.Session{}
	c.mu.Unlock()
	h := &heldEmitter{holding: make(chan struct{}), release: make(chan struct{})}
	t.Cleanup(func() {
		select {
		case <-h.release:
		default:
			close(h.release)
		}
	})
	settle, forwarded := make(chan struct{}), make(chan error, 1)
	go func() { forwarded <- c.forward(settle, h.emit) }()
	event := func(i int) string { return fmt.Sprintf("{\"event\":{\"sequence\":%d}}\n", i) }
	if _, err := io.WriteString(replies, event(0)); err != nil {
		t.Fatal(err)
	}
	<-h.holding // the pump holds event 0: the 64-slot queue takes 1-64, and 65-69 find it full
	go func() {
		for i := 1; i < 70; i++ {
			if _, err := io.WriteString(replies, event(i)); err != nil {
				return
			}
		}
		_ = replies.Close() // the worker exits
	}()
	awaitFault(t, c, "worker event queue full")
	<-c.readDone
	handed := make(chan error, 1)
	go func() {
		handed <- c.handoff(settle, forwarded, func(*aiiosdk.Session, context.Context) error { return nil }, 5*time.Second)
	}()
	<-settle
	close(h.release)
	select {
	case err := <-handed:
		if err == nil || !strings.Contains(err.Error(), "worker event delivery unproven: 5 worker event(s) dropped") {
			t.Fatalf("dropped final events were not reported: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("teardown did not return")
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.got) != 65 {
		t.Fatalf("queued events not handed on: %d of 65", len(h.got))
	}
}

// A reply for an ID already answered is a fault while the lane is live, never
// a second answer and never ignored.
func TestPrivateDuplicateReplyFaultsWhileLive(t *testing.T) {
	c, requests, replies := privateFixture(t)
	p := pendingResult(t, c, "synthesize")
	if !bufio.NewScanner(requests).Scan() {
		t.Fatal("request not written")
	}
	if _, err := io.WriteString(replies, "{\"id\":1,\"result\":{}}\n"); err != nil {
		t.Fatal(err)
	}
	if r := awaitResult(t, p); r.Err != nil {
		t.Fatalf("first reply: %+v", r)
	}
	if _, err := io.WriteString(replies, "{\"id\":1,\"error\":\"LATE\"}\n"); err != nil {
		t.Fatal(err)
	}
	awaitFault(t, c, "unsolicited")
	unanswered(t, p)
}

// Every protocol violation faults the live lane, as before, but none ends the
// reading: what the worker writes after it, its terminal event included, is
// still read to the end of its output and queued for teardown.
func TestReaderDrainsTheWorkersTailAfterAFault(t *testing.T) {
	for _, tc := range []struct{ name, lines, fault string }{
		{"malformed line", "{broken\n", "invalid character"},
		{"unsolicited reply", "{\"id\":999,\"result\":{}}\n", "unsolicited worker reply"},
		{"duplicate readiness", "{\"ready\":{}}\n{\"ready\":{}}\n", "duplicate worker readiness"},
		{"invalid settings request", "{\"id\":3,\"settings_request\":{}}\n", "invalid private settings request"},
		{"unclassifiable message", "{}\n", "unclassifiable worker message"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, _, replies := privateFixture(t)
			terminal := `{"type":"failure","resources_released":true}`
			if _, err := io.WriteString(replies, tc.lines+"{\"id\":5,\"result\":{}}\n{\"event\":"+terminal+"}\n"); err != nil {
				t.Fatal(err)
			}
			awaitFault(t, c, tc.fault)
			_ = replies.Close() // the worker exits
			select {
			case raw := <-c.events:
				if string(raw) != terminal {
					t.Fatalf("queued %s, want the terminal event", raw)
				}
			case <-time.After(time.Second):
				t.Fatal("the terminal event after the fault was never read")
			}
			<-c.readDone
			c.mu.Lock()
			defer c.mu.Unlock()
			if c.unread != nil {
				t.Fatalf("a drained output was reported unread: %v", c.unread)
			}
		})
	}
}

// Output the reader cannot read on ends its reading before the worker's end.
// The reader's retirement is then no proof that the tail was consumed, and
// teardown says so even though everything it was given was flushed.
func TestUnreadWorkerOutputMakesDeliveryUnproven(t *testing.T) {
	c, _, replies := privateFixture(t)
	c.mu.Lock()
	c.session = &aiiosdk.Session{} // the emitter stands in for its writer
	c.mu.Unlock()
	h := &heldEmitter{holding: make(chan struct{}), release: make(chan struct{})}
	close(h.release) // a host that takes everything
	settle, forwarded := make(chan struct{}), make(chan error, 1)
	go func() { forwarded <- c.forward(settle, h.emit) }()
	go func() { // blocks once the reader stops; cleanup's close releases it
		_, _ = io.WriteString(replies, "{\"event\":{\"sequence\":0}}\n"+strings.Repeat("x", 2<<20)+"\n{\"event\":{\"sequence\":1}}\n")
	}()
	awaitFault(t, c, "token too long")
	<-c.readDone
	err := c.handoff(settle, forwarded, func(*aiiosdk.Session, context.Context) error { return nil }, 5*time.Second)
	if err == nil || !strings.Contains(err.Error(), "worker event delivery unproven: the worker's output was not read to its end") || !errors.Is(err, bufio.ErrTooLong) {
		t.Fatalf("a tail the reader never reached was not reported: %v", err)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if !slices.Equal(h.got, []string{`{"sequence":0}`}) {
		t.Fatalf("events before the unreadable line: %v", h.got)
	}
}
