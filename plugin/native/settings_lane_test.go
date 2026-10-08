package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// settingsLaneFixture is a carrier whose settings are read from the host
// the test gives it, with the real reader, lane and ordered writer.
func settingsLaneFixture(t *testing.T, get func(context.Context) (aiiosdk.Object, error)) (*carrier, *bufio.Scanner, func(string)) {
	t.Helper()
	requests, in := io.Pipe()
	out, replies := io.Pipe()
	c := newCarrier(in, out, func() {})
	c.workers.Add(1)
	go func() { defer c.workers.Done(); c.serveSettings(get, nil) }()
	c.startLane()
	go c.read()
	t.Cleanup(func() { c.fail(io.EOF); replies.Close(); requests.Close(); c.workers.Wait(); <-c.readDone })
	return c, bufio.NewScanner(requests), func(line string) {
		t.Helper()
		if _, err := io.WriteString(replies, line+"\n"); err != nil {
			t.Fatal(err)
		}
	}
}

// THE NEWEST SETTINGS REQUEST WINS. The worker answers only its newest
// request. A read for an earlier one is retired the moment a newer one
// arrives, a request that had not begun is replaced, nothing is said for
// either, and no number of requests fails the carrier. Before: requests
// were read in order from a channel of one, so the read of an aborted
// session held the next open's until its limit, and a third request in that
// wait ended the carrier with "private settings capacity exhausted".
func TestTheNewestSettingsRequestWins(t *testing.T) {
	type call struct {
		ctx    context.Context
		answer chan error // nil answers with settings
	}
	calls, over := make(chan call), make(chan struct{})
	c, written, worker := settingsLaneFixture(t, func(ctx context.Context) (aiiosdk.Object, error) {
		mine := call{ctx, make(chan error)}
		select {
		case calls <- mine:
		case <-over:
			return nil, errors.New("the test is over")
		}
		select { // this host does not return until the test says
		case err := <-mine.answer:
			if err != nil {
				return nil, err
			}
		case <-over:
			return nil, errors.New("the test is over")
		}
		return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":{"stt_language":"en"}}}`), nil
	})
	t.Cleanup(func() { close(over) }) // before the fixture's own, which waits for the lane
	next := func(what string) call {
		t.Helper()
		select {
		case got := <-calls:
			return got
		case <-time.After(5 * time.Second):
			t.Fatalf("the host was not asked for %s", what)
			return call{}
		}
	}
	worker(`{"settings_request":{"id":1,"session_id":"first"}}`)
	first := next("the first session")

	// Four more opens while the first read is with the host, then an event:
	// the reader takes lines in order, so once the event is through, all
	// four requests have reached the lane.
	for id := 2; id <= 5; id++ {
		worker(fmt.Sprintf(`{"settings_request":{"id":%d,"session_id":"open-%d"}}`, id, id))
	}
	worker(`{"event":{"type":"progress"}}`)
	select {
	case <-c.events:
	case <-time.After(5 * time.Second):
		t.Fatal("the reader stopped: the requests did not all reach the lane")
	}
	select {
	case err := <-c.fault:
		t.Fatalf("five settings requests failed the carrier: %v", err)
	default:
	}
	select {
	case <-first.ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("the first session's read was left with the host after a newer request arrived")
	}
	first.answer <- first.ctx.Err()

	newest := next("the newest session")
	select {
	case <-newest.ctx.Done():
		t.Fatal("the newest request's read was retired with the one before it")
	default:
	}
	newest.answer <- nil
	if !written.Scan() {
		t.Fatal("no settings outcome reached the worker")
	}
	var out privateRequest
	if err := json.Unmarshal(written.Bytes(), &out); err != nil || out.Settings == nil {
		t.Fatalf("not a settings outcome: %s", written.Bytes())
	}
	// The first line written is the newest request's: nothing was said for
	// the retired read, and the three replaced requests were never read.
	if out.Settings.ID != 5 || out.Settings.SessionID != "open-5" || out.Settings.Reason != "" || string(out.Settings.Values) != `{"stt_language":"en"}` {
		t.Fatalf("the worker was answered for request %d (%s), reason %q: %s", out.Settings.ID, out.Settings.SessionID, out.Settings.Reason, written.Bytes())
	}
	select {
	case <-calls:
		t.Fatal("a replaced request was read after the newest")
	case <-time.After(100 * time.Millisecond):
	}
}

// A READ THAT IS NOT REPLACED IS ANSWERED AS BEFORE: one request, one
// outcome; and the next request after it is read in its turn.
func TestSettingsRequestsOneAfterAnotherAreEachAnswered(t *testing.T) {
	_, written, worker := settingsLaneFixture(t, func(context.Context) (aiiosdk.Object, error) {
		return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":{}}}`), nil
	})
	for id := uint64(1); id <= 3; id++ {
		worker(fmt.Sprintf(`{"settings_request":{"id":%d,"session_id":"s"}}`, id))
		if !written.Scan() {
			t.Fatal("no settings outcome reached the worker")
		}
		var out privateRequest
		if err := json.Unmarshal(written.Bytes(), &out); err != nil || out.Settings == nil || out.Settings.ID != id || out.Settings.Error != "" {
			t.Fatalf("request %d: %s", id, written.Bytes())
		}
	}
}

// A REQUEST IS TAKEN ONCE. A wake with nothing behind it (the request it
// was sent for has already been taken with a later one) reads nothing; and
// taking a request ends nothing but an earlier read.
func TestASettingsRequestIsTakenOnce(t *testing.T) {
	l := settingsLane{wake: make(chan struct{}, 1)}
	if _, _, ok := l.take(context.Background()); ok {
		t.Fatal("a request was taken from an empty lane")
	}
	l.offer(settingsRequest{settingsQuery: settingsQuery{1, "a"}})
	l.offer(settingsRequest{settingsQuery: settingsQuery{2, "b"}})
	q, ctx, ok := l.take(context.Background())
	if !ok || q.ID != 2 || ctx.Err() != nil {
		t.Fatalf("the newest was not taken, live: %+v %v", q, ok)
	}
	if again, _, ok := l.take(context.Background()); ok {
		t.Fatalf("request %d was taken a second time", again.ID)
	}
	if len(l.wake) != 1 {
		t.Fatalf("two offers left %d wakes", len(l.wake))
	}
	l.offer(settingsRequest{settingsQuery: settingsQuery{3, "c"}})
	if ctx.Err() == nil {
		t.Fatal("a newer request left the read in flight running")
	}
	l.finished()
	if q, ctx, ok = l.take(context.Background()); !ok || q.ID != 3 || ctx.Err() != nil {
		t.Fatalf("the request that arrived during a read was lost or taken retired: %+v %v", q, ok)
	}
}

// A REPLY'S QUESTION IS THE SETTINGS ALONE, INSIDE A READ'S TIME. As each
// reply begins an open session asks for the settings in force, so that a
// voice the operator saved is the next reply's. That question reads no
// correction list (the list is a session's, taken at its opening) and is
// given a read's time, not an opening's; a session's opening still reads
// both, inside the opening's time.
func TestARepliesQuestionIsTheSettingsAloneInsideAReadsTime(t *testing.T) {
	type asked struct {
		left time.Duration
	}
	settings, lists := make(chan asked, 4), make(chan struct{}, 4)
	requests, in := io.Pipe()
	out, replies := io.Pipe()
	c := newCarrier(in, out, func() {})
	c.limits = limits{HostRead: 400 * time.Millisecond, HostWrite: 3 * time.Second, StorageWait: 3 * time.Second, DrainIdle: time.Second, ReplySettings: 150 * time.Millisecond, Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: time.Minute, OpeningNotice: 1500 * time.Millisecond}
	c.workers.Add(1)
	go func() {
		defer c.workers.Done()
		c.serveSettings(func(ctx context.Context) (aiiosdk.Object, error) {
			deadline, _ := ctx.Deadline()
			settings <- asked{time.Until(deadline)}
			return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":{"tts_voice":"javert"}}}`), nil
		}, func(context.Context) json.RawMessage { lists <- struct{}{}; return json.RawMessage(`{"revision":1}`) })
	}()
	c.startLane()
	go c.read()
	t.Cleanup(func() { c.fail(io.EOF); replies.Close(); requests.Close(); c.workers.Wait(); <-c.readDone })
	written := bufio.NewScanner(requests)
	answer := func(line string) privateRequest {
		t.Helper()
		if _, err := io.WriteString(replies, line+"\n"); err != nil {
			t.Fatal(err)
		}
		if !written.Scan() {
			t.Fatal("no settings outcome reached the worker")
		}
		var got privateRequest
		if err := json.Unmarshal(written.Bytes(), &got); err != nil || got.Settings == nil || got.Settings.Error != "" {
			t.Fatalf("not a settings outcome: %s", written.Bytes())
		}
		return got
	}
	opening := answer(`{"settings_request":{"id":1,"session_id":"s"}}`)
	if a := <-settings; a.left > 3*time.Second || a.left < 2900*time.Millisecond {
		t.Fatalf("an opening's settings are given the opening's time (3 s): %v", a.left)
	}
	if string(opening.Settings.Corrections) != `{"revision":1}` || len(lists) != 1 {
		t.Fatalf("an opening did not take its correction list: %s", opening.Settings.Corrections)
	}
	<-lists
	reply := answer(`{"settings_request":{"id":2,"session_id":"s","refresh":true}}`)
	if a := <-settings; a.left > 400*time.Millisecond || a.left < 300*time.Millisecond {
		t.Fatalf("a reply's question is given a read's time (400 ms): %v", a.left)
	}
	if reply.Settings.ID != 2 || string(reply.Settings.Values) != `{"tts_voice":"javert"}` {
		t.Fatalf("the settings in force did not reach the worker: %+v", reply.Settings)
	}
	if len(reply.Settings.Corrections) != 0 || len(lists) != 0 {
		t.Fatalf("a reply's question read the correction list: %s", reply.Settings.Corrections)
	}
	// A line that is not a boolean there is not this request.
	if _, err := io.WriteString(replies, `{"settings_request":{"id":3,"session_id":"s","refresh":"yes"}}`+"\n"); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-c.fault:
		if err == nil {
			t.Fatal("a malformed request ended the carrier without a reason")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a request whose refresh is not a boolean was taken")
	}
}
