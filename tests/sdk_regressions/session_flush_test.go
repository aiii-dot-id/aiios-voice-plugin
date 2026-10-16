//go:build !wasm_unknown

// Emit returns when the writer holds a frame, not when the host has it.
// Flush is the wait between the two: nil only once every frame accepted
// before it is written, an ended lane or a fault said as such, and a
// context that ends first said as an unknown outcome. These proofs hold
// the writer at exact frames with a host seam, never with sleeps.
package aiiosdk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

// flushHost is a slow but healthy host, frame by frame: the writer is
// held at the first byte of each frame until the test lets it through or
// fails it, and every frame that got through whole is recorded. A held
// frame has left the queue and is not written — exactly the interval in
// which a frame is accepted, dequeued and still not the host's.
type flushHost struct {
	mu       sync.Mutex
	frames   [][]byte // payloads written whole, in order
	half     bool     // the current frame's header is written
	onFrame  func([]byte)
	held     chan int   // the writer is held at the start of host frame n
	verdict  chan error // nil lets the held frame through; an error fails it
	open     chan struct{}
	openOnce sync.Once
}

func (h *flushHost) Write(p []byte) (int, error) {
	h.mu.Lock()
	header, n := !h.half, len(h.frames)+1
	h.mu.Unlock()
	if header {
		select {
		case h.held <- n:
			select {
			case err := <-h.verdict:
				if err != nil {
					return 0, err
				}
			case <-h.open:
			}
		case <-h.open:
		}
	}
	h.mu.Lock()
	if h.half {
		frame := append([]byte(nil), p...)
		h.frames = append(h.frames, frame)
		if h.onFrame != nil {
			h.onFrame(frame)
		}
	}
	h.half = !h.half
	h.mu.Unlock()
	return len(p), nil
}

// at waits until the writer is held at the start of host frame n.
func (h *flushHost) at(t *testing.T, n int) {
	t.Helper()
	select {
	case got := <-h.held:
		if got != n {
			t.Fatalf("writer held at frame %d, want %d", got, n)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("writer never reached frame %d", n)
	}
}

func (h *flushHost) pass()              { h.verdict <- nil }
func (h *flushHost) fail(err error)     { h.verdict <- err }
func (h *flushHost) release()           { h.openOnce.Do(func() { close(h.open) }) }
func (h *flushHost) written() int       { h.mu.Lock(); defer h.mu.Unlock(); return len(h.frames) }
func (h *flushHost) frame(i int) string { h.mu.Lock(); defer h.mu.Unlock(); return string(h.frames[i]) }

// flushLane is a standalone lane writing to a flushHost.
func flushLane(t *testing.T) (*flushHost, *Session) {
	t.Helper()
	h := &flushHost{held: make(chan int), verdict: make(chan error), open: make(chan struct{})}
	s := &Session{out: h, pending: make(map[uint64]chan sessionReply), closed: make(chan struct{}), admissionSlots: make(chan struct{}, sessionAdmitQueue)}
	t.Cleanup(func() {
		s.end()
		h.release()
		s.startWriter()
		select {
		case <-s.writerDone:
		case <-time.After(5 * time.Second):
			t.Error("writer did not retire")
		}
	})
	return h, s
}

// noticeWait is a context that says when its holder first waits on it.
// Flush first waits only after it has fixed the frames it covers; a host
// call first waits only after its frame is queued.
type noticeWait struct {
	context.Context
	once    sync.Once
	waiting chan struct{}
}

func (c *noticeWait) Done() <-chan struct{} {
	c.once.Do(func() { close(c.waiting) })
	return c.Context.Done()
}

func waitNoticed(ctx context.Context) *noticeWait {
	return &noticeWait{Context: ctx, waiting: make(chan struct{})}
}

type flushResult struct {
	err     error
	written int // frames the host had whole when Flush returned
}

// startFlush runs Flush on its own goroutine and returns once it is
// waiting: every frame accepted before now is covered, none after.
func startFlush(t *testing.T, s *Session, h *flushHost, ctx context.Context) <-chan flushResult {
	t.Helper()
	nctx := waitNoticed(ctx)
	out := make(chan flushResult, 1)
	go func() {
		err := s.Flush(nctx)
		out <- flushResult{err, h.written()}
	}()
	select {
	case <-nctx.waiting:
	case r := <-out:
		t.Fatalf("Flush returned before it waited: %v", r.err)
	case <-time.After(5 * time.Second):
		t.Fatal("Flush never waited")
	}
	return out
}

func flushEvent(i int) map[string]any {
	return map[string]any{"type": "attribution", "sequence": i, "pad": strings.Repeat("x", 32<<10)}
}

func emitFlushEvents(t *testing.T, s *Session, from, to int) {
	t.Helper()
	for i := from; i <= to; i++ {
		if err := s.Emit(flushEvent(i)); err != nil {
			t.Fatal(err)
		}
	}
}

func cancelled() context.Context {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	return ctx
}

// The carrier's case: 32 KB events behind a slow host. Every Emit returns
// while nothing is the host's; Flush returns only when the last of them is
// written — not when it leaves the queue.
func TestFlushReturnsOnlyOnceEveryAcceptedFrameIsWritten(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	// The writer holds the first event; the host is not reading. Behind
	// it go 64 more, the events' whole share of the queue.
	h.at(t, 1)
	const backlog = 64
	emitFlushEvents(t, s, 1, backlog)
	if got := h.written(); got != 0 {
		t.Fatalf("fixture: %d frames written while the host was held", got)
	}
	// Exiting here loses all 65 frames, and Emit said nothing about it.
	flushed := startFlush(t, s, h, context.Background())
	for n := 1; n <= backlog; n++ {
		h.pass()
		h.at(t, n+1) // frame n is written whole
		select {
		case r := <-flushed:
			t.Fatalf("Flush returned %v with %d of %d frames written", r.err, r.written, backlog+1)
		default:
		}
	}
	// The last frame has left the queue and is not written. A done
	// context makes Flush say what it knows now.
	if err := s.Flush(cancelled()); err == nil || !errors.Is(err, context.Canceled) || errors.Is(err, ErrLaneEnded) {
		t.Fatalf("a dequeued, unwritten frame was reported as written: %v", err)
	}
	h.pass()
	select {
	case r := <-flushed:
		if r.err != nil || r.written != backlog+1 {
			t.Fatalf("Flush: %v with %d of %d frames written", r.err, r.written, backlog+1)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Flush did not return once its last frame was written")
	}
	if err := s.Flush(cancelled()); err != nil {
		t.Fatalf("every frame is written, yet a check with a done context failed: %v", err)
	}
	for i := 0; i <= backlog; i++ {
		var m struct {
			Params struct{ Sequence int }
		}
		if err := json.Unmarshal([]byte(h.frame(i)), &m); err != nil || m.Params.Sequence != i {
			t.Fatalf("frame %d out of order: sequence %d (%v)", i, m.Params.Sequence, err)
		}
	}
}

// Frames accepted after Flush began — events and an admitted answer — are
// not waited for, even while the host holds the first of them.
func TestFlushDoesNotWaitForFramesAcceptedAfterItBegan(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	h.at(t, 1)
	emitFlushEvents(t, s, 1, 3)
	flushed := startFlush(t, s, h, context.Background())
	emitFlushEvents(t, s, 4, 7)
	s.admissionSlots <- struct{}{}
	(&Control{reply: &controlReply{s: s, id: json.RawMessage("1")}}).Answer(true, nil)
	for n := 1; n <= 4; n++ {
		h.pass()
		h.at(t, n+1)
	}
	// The writer now holds the first later frame, and keeps holding it.
	select {
	case r := <-flushed:
		if r.err != nil || r.written != 4 {
			t.Fatalf("Flush: %v with %d frames written, want nil with 4", r.err, r.written)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Flush waited for frames accepted after it began")
	}
}

// A write that fails ends the lane, and no Flush still owed a frame
// returns nil — not while the writer drops the rest, and not after it has
// retired. A Flush whose frames were all written before the fault keeps
// its nil.
func TestFlushReportsTheFaultThatStoppedItsFrames(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	h.at(t, 1)
	early := startFlush(t, s, h, context.Background()) // frame 1 only
	emitFlushEvents(t, s, 1, 4)
	late := startFlush(t, s, h, context.Background()) // frames 1-5
	h.pass()
	h.at(t, 2)
	broken := errors.New("injected: the host's pipe broke")
	h.fail(broken)
	for name, flushed := range map[string]<-chan flushResult{"early": early, "late": late} {
		select {
		case r := <-flushed:
			if name == "early" && r.err != nil {
				t.Fatalf("early Flush's frame was written before the fault: %v", r.err)
			}
			if name == "late" && (!errors.Is(r.err, ErrLaneEnded) || !errors.Is(r.err, broken) || errors.Is(r.err, context.Canceled)) {
				t.Fatalf("late Flush: %v", r.err)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("%s Flush did not return after the fault", name)
		}
	}
	select {
	case <-s.writerDone: // frames 3-5 reported unwritten; the writer retired
	case <-time.After(5 * time.Second):
		t.Fatal("writer did not retire after the fault")
	}
	if err := s.Flush(context.Background()); !errors.Is(err, ErrLaneEnded) || !errors.Is(err, broken) {
		t.Fatalf("after the writer finished with every frame, Flush claimed them: %v", err)
	}
	if got := h.written(); got != 1 {
		t.Fatalf("host has %d frames, want 1", got)
	}
}

// The lane ends — the host's EOF, or a fault while the writer is stuck in
// a write — with covered frames still queued: ErrLaneEnded, never nil and
// never a context error, and a later Flush does not wait.
func TestFlushReportsALaneThatEndsWithFramesStillQueued(t *testing.T) {
	cause := errors.New("injected: an admission handler never returned")
	for name, stop := range map[string]func(*Session){
		"ended":   func(s *Session) { s.end() },
		"faulted": func(s *Session) { s.setFault(cause) },
	} {
		t.Run(name, func(t *testing.T) {
			h, s := flushLane(t)
			emitFlushEvents(t, s, 0, 0)
			h.at(t, 1)
			emitFlushEvents(t, s, 1, 3)
			flushed := startFlush(t, s, h, context.Background())
			stop(s)
			select {
			case r := <-flushed:
				if !errors.Is(r.err, ErrLaneEnded) || errors.Is(r.err, context.Canceled) || r.written != 0 {
					t.Fatalf("Flush: %v with %d written", r.err, r.written)
				}
				if name == "faulted" && !errors.Is(r.err, cause) {
					t.Fatalf("the fault's cause was lost: %v", r.err)
				}
			case <-time.After(5 * time.Second):
				t.Fatal("Flush did not return when the lane ended")
			}
			if err := s.Flush(context.Background()); !errors.Is(err, ErrLaneEnded) {
				t.Fatalf("Flush after the lane ended: %v", err)
			}
		})
	}
}

// A context that ends first is an unknown outcome — distinguishable from
// an ended lane — returned at once, leaving no goroutine behind and the
// frames still owed: the host gets them when it reads again.
func TestACancelledFlushIsAnUnknownOutcomeAndLeavesNothingBehind(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	h.at(t, 1)
	emitFlushEvents(t, s, 1, 3)
	ctx, cancel := context.WithCancel(context.Background())
	flushed := startFlush(t, s, h, ctx)
	cancel()
	select {
	case r := <-flushed:
		if !errors.Is(r.err, context.Canceled) || errors.Is(r.err, ErrLaneEnded) {
			t.Fatalf("cancelled Flush: %v", r.err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled Flush did not return")
	}
	expired, cancelExpired := context.WithDeadline(context.Background(), time.Now())
	defer cancelExpired()
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := s.Flush(expired); !errors.Is(err, context.DeadlineExceeded) {
				t.Errorf("expired Flush: %v", err)
			}
		}()
	}
	wg.Wait()
	buf := make([]byte, 1<<20)
	if stacks := string(buf[:runtime.Stack(buf, true)]); strings.Contains(stacks, "(*Session).Flush") {
		t.Fatalf("a goroutine is still inside Flush:\n%s", stacks)
	}
	for n := 1; n <= 4; n++ {
		h.pass()
		if n < 4 {
			h.at(t, n+1)
		}
	}
	if err := s.Flush(context.Background()); err != nil || h.written() != 4 {
		t.Fatalf("the frames were lost to a cancelled Flush: %v, %d written", err, h.written())
	}
}

// A host call withdrawn before the writer took it was told "not sent"; it
// does not hold a Flush that covers it.
func TestAWithdrawnHostCallDoesNotHoldFlush(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	h.at(t, 1)
	ctx, cancel := context.WithCancel(context.Background())
	nctx := waitNoticed(ctx)
	called := make(chan error, 1)
	go func() { _, err := s.HostCall(nctx, "voice.observe", map[string]any{}); called <- err }()
	<-nctx.waiting // its frame is queued
	cancel()
	if err := <-called; err == nil || !strings.Contains(err.Error(), "not sent") {
		t.Fatalf("fixture: the host call was not withdrawn: %v", err)
	}
	emitFlushEvents(t, s, 1, 1)
	flushed := startFlush(t, s, h, context.Background())
	h.pass()
	h.at(t, 2) // the withdrawn frame was skipped; the second event is next
	h.pass()
	select {
	case r := <-flushed:
		if r.err != nil || r.written != 2 {
			t.Fatalf("Flush: %v with %d written", r.err, r.written)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a withdrawn host call held Flush")
	}
	if strings.Contains(h.frame(0)+h.frame(1), "invoke.call") {
		t.Fatal("the withdrawn host call reached the host")
	}
}

// A Flush racing a lane's very first frame — before the writer exists —
// reads nothing the writer's start writes outside the lock. Repeated so
// that, under -race, each may go first.
func TestAFlushBesideTheFirstFrameIsRaceFree(t *testing.T) {
	for i := 0; i < 100; i++ {
		s := &Session{out: io.Discard, closed: make(chan struct{})}
		start := make(chan struct{})
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			<-start
			if err := s.Flush(context.Background()); err != nil {
				t.Errorf("Flush beside the first frame: %v", err)
			}
		}()
		go func() {
			defer wg.Done()
			<-start
			if err := s.Emit(map[string]int{"sequence": i}); err != nil {
				t.Errorf("first frame: %v", err)
			}
		}()
		close(start)
		wg.Wait()
		if err := s.Flush(context.Background()); err != nil {
			t.Fatal(err)
		}
		s.end()
		<-s.writerDone
	}
}

// Flush beside every other producer, from many goroutines: each producer's
// own frames are the host's when its Flush returns, nothing is lost or
// duplicated, and each producer's order is kept.
func TestFlushIsSafeBesideConcurrentEventsAnswersAndHostCalls(t *testing.T) {
	h, s := flushLane(t)
	h.release() // a healthy host, reading as fast as frames are written
	// The host's record, kept by onFrame under h.mu and read under it.
	last := map[int]int{}
	replies := map[string]bool{}
	calls := 0
	var faults []string
	h.onFrame = func(p []byte) {
		var m struct {
			ID     json.RawMessage
			Method string
			Params struct{ Producer, Sequence int }
		}
		if err := json.Unmarshal(p, &m); err != nil {
			faults = append(faults, err.Error())
			return
		}
		switch m.Method {
		case "session.event":
			if m.Params.Sequence != last[m.Params.Producer]+1 {
				faults = append(faults, fmt.Sprintf("producer %d: %d after %d", m.Params.Producer, m.Params.Sequence, last[m.Params.Producer]))
			}
			last[m.Params.Producer] = m.Params.Sequence
		case "invoke.call":
			calls++
			s.deliverReply(m.ID, sessionReply{result: json.RawMessage(`{"ok":true}`)})
		default:
			if replies[string(m.ID)] {
				faults = append(faults, "duplicate reply "+string(m.ID))
			}
			replies[string(m.ID)] = true
		}
	}
	seen := func(check func() bool) bool {
		h.mu.Lock()
		defer h.mu.Unlock()
		return check()
	}
	flush := func(who string) bool {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := s.Flush(ctx); err != nil {
			t.Errorf("%s: Flush: %v", who, err)
			return false
		}
		return true
	}
	const each = 40
	var wg sync.WaitGroup
	for p := 1; p <= 4; p++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 1; i <= each; i++ {
				if err := s.Emit(map[string]any{"producer": p, "sequence": i}); err != nil {
					t.Errorf("emit: %v", err)
					return
				}
				if i%8 == 0 {
					if !flush("emitter") {
						return
					}
					if !seen(func() bool { return last[p] >= i }) {
						t.Errorf("producer %d: Flush returned before event %d was written", p, i)
						return
					}
				}
			}
		}()
	}
	for a := 1; a <= 2; a++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 1; i <= each; i++ {
				id := fmt.Sprint(a*1000 + i)
				s.admissionSlots <- struct{}{}
				(&Control{reply: &controlReply{s: s, id: json.RawMessage(id)}}).Answer(true, nil)
				if i%8 == 0 {
					if !flush("answerer") {
						return
					}
					if !seen(func() bool { return replies[id] }) {
						t.Errorf("answer %s: Flush returned before it was written", id)
						return
					}
				}
			}
		}()
	}
	for c := 1; c <= 2; c++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 10; i++ {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				_, err := s.HostCall(ctx, "voice.observe", map[string]any{})
				cancel()
				if err != nil {
					t.Errorf("host call: %v", err)
					return
				}
				if !flush("caller") {
					return
				}
			}
		}()
	}
	stop := make(chan struct{})
	var flushers sync.WaitGroup
	for f := 0; f < 2; f++ {
		flushers.Add(1)
		go func() {
			defer flushers.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				if !flush("flusher") {
					return
				}
				runtime.Gosched()
			}
		}()
	}
	wg.Wait()
	close(stop)
	flushers.Wait()
	if !flush("final") {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(faults) != 0 {
		t.Fatalf("delivery faults: %v", faults)
	}
	for p := 1; p <= 4; p++ {
		if last[p] != each {
			t.Fatalf("producer %d: %d of %d events written", p, last[p], each)
		}
	}
	if len(replies) != 2*each || calls != 20 || s.faultErr() != nil {
		t.Fatalf("replies=%d calls=%d fault=%v", len(replies), calls, s.faultErr())
	}
}

// The 64-event backlog of TestEventBacklogPreservesEveryAdmittedControlReply,
// with a Flush pending over it: all 64 admitted answers are still accepted
// into their reserved space, every one is delivered, and the Flush still
// resolves.
func TestAPendingFlushTakesNoReservedReplyCapacity(t *testing.T) {
	h, s := flushLane(t)
	emitFlushEvents(t, s, 0, 0)
	h.at(t, 1)
	const events = 64
	emitFlushEvents(t, s, 1, events)
	before := startFlush(t, s, h, context.Background())
	for i := 1; i <= sessionAdmitQueue; i++ {
		s.admissionSlots <- struct{}{}
		(&Control{reply: &controlReply{s: s, id: json.RawMessage(fmt.Sprint(i))}}).Answer(map[string]bool{"accepted": true}, nil)
	}
	if err := s.faultErr(); err != nil {
		t.Fatalf("a pending Flush displaced a control reply: %v", err)
	}
	if got := len(s.admissionSlots); got != sessionAdmitQueue {
		t.Fatalf("undelivered replies relinquished their reserved slots: %d", got)
	}
	after := startFlush(t, s, h, context.Background())
	h.release()
	for name, flushed := range map[string]<-chan flushResult{"before": before, "after": after} {
		select {
		case r := <-flushed:
			if r.err != nil {
				t.Fatalf("%s Flush: %v", name, r.err)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("%s Flush never resolved", name)
		}
	}
	if len(s.admissionSlots) != 0 {
		t.Fatal("delivered answers retained their slots")
	}
	seen := map[int]bool{}
	notices := 0
	for i := 0; i < h.written(); i++ {
		var m struct {
			ID     int
			Method string
			Result json.RawMessage
		}
		if err := json.Unmarshal([]byte(h.frame(i)), &m); err != nil {
			t.Fatal(err)
		}
		if m.Method != "" {
			notices++
			continue
		}
		if m.ID < 1 || m.ID > sessionAdmitQueue || seen[m.ID] || len(m.Result) == 0 {
			t.Fatalf("invalid or duplicate reply: %s", h.frame(i))
		}
		seen[m.ID] = true
	}
	if notices != events+1 || len(seen) != sessionAdmitQueue {
		t.Fatalf("lost delivery: events=%d replies=%d", notices, len(seen))
	}
}
