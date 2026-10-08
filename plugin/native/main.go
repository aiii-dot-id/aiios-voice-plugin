// Native T3 carrier: the public wire is the actual Go Plugin SDK. The private
// worker owns models and receives audio on separate inherited handles.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

type workerMessage struct {
	ID       uint64           `json:"id"`
	Result   json.RawMessage  `json:"result"`
	Error    string           `json:"error"`
	Event    json.RawMessage  `json:"event"`
	Ready    json.RawMessage  `json:"ready"`
	Settings *settingsRequest `json:"settings_request,omitempty"`
	Snapshot *snapshotQuery   `json:"snapshot_request,omitempty"`
}

// Fixtures retain an explicitly unprofiled lane; every real worker must prove
// loaded models and measured inference before the SDK advertises readiness.
//
// ONLY WHAT THE NATIVE WORKER REPORTS IS READINESS. It reports four models,
// or five with speaker identification, under one of three backends, each
// with its own accelerator word (native_c_api.cpp, warm). The carrier also
// took three models under "metal", "cuda" or "directml" with no backend
// named: the Python engines' reports, which no released engine writes and
// whose profile the carrier now refuses at start (runtime.go). A worker that
// was handed a recognizer from outside says "external_recognizer", which is
// no measured placement, and is refused as it always was.
//
// warmProbe is the table's warm_probe_ms: what the warm inference of a
// worker's start may take. The worker's library is handed the same member and
// fails a start past it, so no report of a longer one comes from it; one that
// does come is refused here with both numbers. It was 40 seconds typed here
// and typed again in the library.
func readinessReport(raw json.RawMessage, warmProbe time.Duration) (*aiiosdk.ReadyReport, error) {
	var body struct {
		Identity struct {
			Backend string `json:"backend"`
		} `json:"identity"`
		Readiness *struct {
			Models      int    `json:"models_loaded"`
			Accelerator string `json:"accelerator"`
			ProbeMS     int    `json:"probe_ms"`
		} `json:"readiness"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		return nil, err
	}
	if body.Readiness == nil {
		if body.Identity.Backend == "deterministic-test-not-real-model" {
			return nil, nil
		}
		return nil, errors.New("worker omitted measured readiness")
	}
	r := body.Readiness
	native := map[string]string{"native-common-cpu": "cpu", "native-common-vulkan": "cpu_vulkan", "native-common-metal": "cpu_metal"}
	accelerator, known := native[body.Identity.Backend]
	if !known || r.Accelerator != accelerator || (r.Models != 4 && r.Models != 5) || r.ProbeMS <= 0 {
		return nil, errors.New("invalid worker warm-inference readiness")
	}
	if int64(r.ProbeMS) > warmProbe.Milliseconds() {
		return nil, fmt.Errorf("invalid worker warm-inference readiness: the warm inference took %d ms, more than the %d ms the limits table gives it (warm_probe_ms)", r.ProbeMS, warmProbe.Milliseconds())
	}
	return &aiiosdk.ReadyReport{ModelsLoaded: r.Models, Accelerator: r.Accelerator, ProbeMS: r.ProbeMS}, nil
}

type carrier struct {
	cmd       *exec.Cmd
	in        io.WriteCloser
	out       io.ReadCloser
	writes    chan privateRequest
	pending   map[uint64]*privatePending
	stop      chan struct{}
	workers   sync.WaitGroup
	interrupt func()
	failOnce  sync.Once
	events    chan json.RawMessage
	ready     chan workerMessage
	fault     chan error
	done      chan error
	readDone  chan struct{}
	mu        sync.Mutex
	session   *aiiosdk.Session
	readySeen bool   // one readiness per worker lifetime; guarded by mu
	dropped   int    // worker events read but never queued; guarded by mu
	unread    error  // why the reader stopped before the worker's output ended; guarded by mu
	id        uint64 // owned by SDK admission goroutine
	settings  settingsLane
	snapshots chan snapshotQuery
	ctx       context.Context
	cancel    context.CancelFunc
	// limits is every time limit this carrier waits by (limits.go); set
	// once before the worker starts and read-only after.
	limits limits
	// began is the instant the wait for the worker's readiness is counted
	// from (awaitReady), and workerStarted how long after it the worker was
	// started.
	began         time.Time
	workerStarted time.Duration

	// vocabulary serializes confirmed changes to the correction list.
	vocabulary sync.Mutex
}

type privateRequest struct {
	ID        uint64          `json:"id,omitempty"`
	Operation string          `json:"operation,omitempty"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
	Settings  *settingsReply  `json:"settings_reply,omitempty"`
	Snapshot  *snapshotReply  `json:"snapshot_reply,omitempty"`
}
type privatePending struct {
	answer   func(any, error)
	deadline time.Time
	written  bool // the writer took it, so the worker may see it; guarded by mu
}

func newCarrier(in io.WriteCloser, out io.ReadCloser, interrupt func()) *carrier {
	ctx, cancel := context.WithCancel(context.Background())
	return &carrier{in: in, out: out, writes: make(chan privateRequest, 64),
		pending: make(map[uint64]*privatePending), stop: make(chan struct{}), interrupt: interrupt,
		events: make(chan json.RawMessage, 64), ready: make(chan workerMessage, 1),
		fault: make(chan error, 1), done: make(chan error, 1), readDone: make(chan struct{}),
		settings: settingsLane{wake: make(chan struct{}, 1)}, snapshots: make(chan snapshotQuery, 1), ctx: ctx, cancel: cancel, limits: defaultLimits,
		began: time.Now()} // run moves it back to this process's own start
}

// say writes to this carrier's log, now, a sentence that is also in the
// reason it ends for: whoever stops waiting for this process before it has
// ended has still been told.
func say(err error) { fmt.Fprintln(os.Stderr, "aii-voice-t3:", err) }

func (c *carrier) fail(err error) {
	c.failOnce.Do(func() {
		c.cancel()
		c.mu.Lock()
		close(c.stop)
		unsent := make([]*privatePending, 0, len(c.pending))
		for id, p := range c.pending {
			if !p.written {
				unsent = append(unsent, p)
			}
			delete(c.pending, id)
		}
		c.mu.Unlock()
		// Only a request that never left the ordered queue is refused: the
		// worker cannot have seen it. One the writer took may have been
		// admitted, so it is never answered with an error, which the host
		// reads as a definite refusal. The lane ends with it unanswered, and
		// the host reports that as admission unknown.
		for _, p := range unsent {
			p.answer(nil, fmt.Errorf("worker request not sent: %w", err))
		}
		c.fault <- err
		_ = c.in.Close() // wakes the one private writer, including a blocked write
		c.interrupt()    // the actual public SDK reader, never a fabricated refusal
	})
}

// One ordered writer and one deadline owner; never one write goroutine per
// request. The retained control is answered by the one reply reader, never by
// a per-request waiter and never while holding the private admission lock.
func (c *carrier) startPrivate() {
	c.startSettings()
	c.startSnapshots()
	c.startLane()
}

// startLane starts the one ordered writer to the worker and the one owner of
// the controls' deadlines.
func (c *carrier) startLane() {
	c.workers.Add(2)
	go func() {
		defer c.workers.Done()
		encoder := json.NewEncoder(c.in)
		for {
			select {
			case <-c.stop:
				return
			case req := <-c.writes:
				// Claimed under the admission lock before its first byte: fail
				// either finds it written or has already stopped this writer.
				c.mu.Lock()
				select {
				case <-c.stop:
					c.mu.Unlock()
					return
				default:
				}
				if p := c.pending[req.ID]; p != nil {
					p.written = true
				}
				c.mu.Unlock()
				if err := encoder.Encode(req); err != nil {
					c.fail(err)
					return
				}
			}
		}
	}()
	go func() {
		defer c.workers.Done()
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-c.stop:
				return
			case now := <-ticker.C:
				c.mu.Lock()
				expired := false
				for _, p := range c.pending {
					if !now.Before(p.deadline) {
						expired = true
						break
					}
				}
				c.mu.Unlock()
				if expired {
					c.fail(errors.New("worker admission timeout"))
					return
				}
			}
		}
	}()
}

// read is the one reader of the worker's output. While the lane is live it
// holds the worker to the private protocol: any violation faults the lane. A
// fault never ends the reading. Once the lane has failed, fail has emptied
// pending and every later fault is a no-op, so the reader drains: events are
// still queued for teardown to hand on, a reply answers nothing, and reading
// goes on to the end of the worker's output. A terminal event behind a late
// reply or a broken line is not lost, and a retiring worker is not left
// blocked on a full pipe. Only output the scanner cannot read on ends the
// reading early, and teardown reports what followed it unproven.
func (c *carrier) read() {
	defer close(c.readDone)
	scanner := bufio.NewScanner(c.out)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	for scanner.Scan() {
		var m workerMessage
		if err := json.Unmarshal(scanner.Bytes(), &m); err != nil {
			c.fail(err)
			continue
		}
		if m.Snapshot != nil {
			if m.Settings != nil || m.ID != 0 || m.Error != "" || len(m.Result)+len(m.Event)+len(m.Ready) != 0 || !m.Snapshot.valid() {
				c.fail(errors.New("invalid private snapshot request"))
				continue
			}
			if !m.Snapshot.workers() {
				c.fail(errors.New("private snapshot request for a resource that is not the worker's"))
				continue
			}
			select {
			case c.snapshots <- *m.Snapshot: // its server answers nothing once the lane has failed
			default:
				c.fail(errors.New("private snapshot capacity exhausted"))
			}
		} else if m.Settings != nil {
			if m.ID != 0 || m.Error != "" || len(m.Result)+len(m.Event)+len(m.Ready) != 0 || !m.Settings.valid() {
				c.fail(errors.New("invalid private settings request"))
				continue
			}
			c.settings.offer(*m.Settings) // the newest is the one the worker waits for
		} else if len(m.Ready) != 0 {
			// A worker announces readiness once. The channel's capacity of one
			// refused a second announcement only while the first still sat in
			// it; once run() had taken it, a repeat was buffered and forgotten.
			c.mu.Lock()
			seen := c.readySeen
			c.readySeen = true
			c.mu.Unlock()
			if seen {
				c.fail(errors.New("duplicate worker readiness"))
				continue
			}
			c.ready <- m // capacity one, and this is the only send
		} else if len(m.Event) != 0 {
			select {
			case c.events <- m.Event:
			default:
				// A full queue faults the lane. This event and every later
				// one that finds it full are counted for teardown to report,
				// and reading goes on so the worker's exit is still seen.
				c.mu.Lock()
				c.dropped++
				c.mu.Unlock()
				c.fail(errors.New("worker event queue full"))
			}
		} else if m.ID != 0 {
			c.mu.Lock()
			p := c.pending[m.ID]
			if p != nil {
				delete(c.pending, m.ID)
			}
			c.mu.Unlock()
			if p == nil {
				// Unknown or already answered, a fault while the lane is live.
				// Once it has failed this is a late reply: its control stays
				// unanswered, admission unknown to the host, and what the
				// worker says after it is still read.
				c.fail(errors.New("unsolicited worker reply"))
				continue
			}
			var refusal error
			if m.Error != "" {
				refusal = errors.New(m.Error)
			}
			p.answer(m.Result, refusal)
		} else {
			c.fail(errors.New("unclassifiable worker message"))
		}
	}
	if err := scanner.Err(); err != nil {
		c.mu.Lock()
		c.unread = err
		c.mu.Unlock()
		c.fail(err)
	} else {
		c.fail(io.EOF)
	}
}

// forward is the one owner of worker events on the public lane, in the order
// the reader queued them. Once settle closes it hands on only what is already
// queued, then returns; its error is the event it could not hand on.
func (c *carrier) forward(settle <-chan struct{}, emit func(*aiiosdk.Session, any) error) error {
	for {
		var raw json.RawMessage
		select {
		case raw = <-c.events:
		case <-settle:
			select {
			case raw = <-c.events:
			default:
				return nil
			}
		}
		c.mu.Lock()
		s := c.session
		c.mu.Unlock()
		var err error
		if s == nil {
			err = errors.New("event before first admission")
		} else if err = emit(s, raw); err == nil {
			continue
		}
		select {
		case <-settle: // teardown: the lane's fault, if any, is already recorded
		default:
			c.fail(err)
		}
		return err
	}
}

// handoff settles worker events while a private fault leaves the public lane
// open: within one bound, what the reader took is handed to the public writer
// and flushed, proven written, or the carrier says its delivery is unproven.
// Handed on is not written, and this fault path exits the process: frames
// the writer still held would be lost. A stalled host never holds retirement
// open, and nothing undelivered is dropped in silence. The bound is the
// table's lane_flush_ms, and when it is what passed the carrier says so with
// the number.
func (c *carrier) handoff(settle chan<- struct{}, forwarded <-chan error, flush func(*aiiosdk.Session, context.Context) error) (err error) {
	bound := c.limits.LaneFlush
	ctx, cancel := context.WithTimeout(context.Background(), bound)
	defer cancel()
	late := func(what string) error {
		return fmt.Errorf("worker event delivery unproven: %s in %d ms, the time the limits table gives the host's lane at this carrier's end (lane_flush_ms)", what, bound.Milliseconds())
	}
	defer func() { // a flush proves only what it was given
		c.mu.Lock()
		dropped, unread := c.dropped, c.unread
		c.mu.Unlock()
		if dropped != 0 {
			err = errors.Join(err, fmt.Errorf("worker event delivery unproven: %d worker event(s) dropped with the event queue full", dropped))
		}
		if unread != nil {
			err = errors.Join(err, fmt.Errorf("worker event delivery unproven: the worker's output was not read to its end: %w", unread))
		}
	}()
	select {
	case <-c.readDone: // the reader retired: the queue can only shrink, and unread says whether it saw the end
	case <-ctx.Done():
		close(settle)
		return late("the worker's output was still being read")
	}
	close(settle)
	select {
	case err := <-forwarded:
		if err != nil {
			return fmt.Errorf("worker events not delivered: %w", err)
		}
	case <-ctx.Done():
		return late("the host lane did not take them")
	}
	c.mu.Lock()
	s := c.session
	c.mu.Unlock()
	if s == nil {
		return nil // no lane was ever used: nothing was handed on
	}
	if err := flush(s, ctx); err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return fmt.Errorf("%w: %w", late("the host lane did not confirm them written"), err)
		}
		return fmt.Errorf("worker event delivery unproven: %w", err)
	}
	return nil
}

func (c *carrier) admit(control *aiiosdk.Control) {
	if err := validateEnrollment(control.Op, control.Args); err != nil {
		control.Answer(nil, err)
		return
	}
	if err := validateWaveform(control.Op, control.Args); err != nil {
		control.Answer(nil, err)
		return
	}
	if err := validateVocabulary(control.Op, control.Args); err != nil {
		control.Answer(nil, err)
		return
	}
	if vocabularyOperation(control.Op) {
		c.vocabularyStore(control)
		return
	}
	if control.Op == "recording.list" || control.Op == "recording.delete" || control.Op == "recording.prune" {
		c.recordingStore(control)
		return
	}
	if err := c.enqueue(control.Session, control.Op, control.Args, control.Answer); err != nil {
		control.Answer(nil, err)
	}
}

func (c *carrier) enqueue(s *aiiosdk.Session, op string, args aiiosdk.Object, answer func(any, error)) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	select {
	case <-c.stop:
		return errors.New("worker lane ended")
	default:
	}
	if len(c.pending) >= 64 {
		return errors.New("worker admission capacity exhausted")
	}
	c.session = s
	c.id++
	id := c.id
	request := privateRequest{ID: id, Operation: op, Arguments: append(json.RawMessage(nil), args...)}
	// A control that uses the private files is given what its storage may
	// take by the table, computed and not typed beside it: at this limit
	// the carrier fails itself, which it must never do over an operation
	// that is still inside its own limits (it was 45 s beside reads of 10 s
	// each).
	p := &privatePending{answer: answer, deadline: time.Now().Add(c.controlWait(op))}
	c.pending[id] = p // register BEFORE enqueue: reply may precede our return
	select {
	case c.writes <- request:
		return nil // enqueued, not an admission verdict; only the worker answers
	default:
		delete(c.pending, id)
		return errors.New("worker request not sent: ordered queue full")
	}
}

// controlWait is how long the worker has to answer one control: what its
// storage may take for an operation that uses the private files; for a
// playback report, which the worker holds until the audio write it counts
// has ended, that write's time and a control's (limits.playbackReport); and
// the table's control limit for every other. Bounded private publication
// and readback; never blocks admission.
func (c *carrier) controlWait(op string) time.Duration {
	if enrollmentOperation(op) || op == "recording.record" {
		return c.limits.storageOperation()
	}
	if op == aiiosdk.OpSessionPlaybackReport {
		return c.limits.playbackReport()
	}
	return c.limits.Control
}

// awaitReady waits for the worker to report ready until the table's ready_ms
// have passed since c.began. In run that is startupBegan: the instant this
// process initialised its package variables, before main and before anything
// of the runtime is read, and the instant its start-up records count from.
// The check of the runtime's files and the worker's spawn are therefore
// inside the wait, as they are inside the allowance the host gives a start;
// counted from the worker's start, as it was, a slow check came on top and
// the host's allowance could pass first. Where that time is already spent
// when the worker has been started there is nothing left to wait, and the
// sentence is said at once. A worker that fails first, and a fault of the
// lane, end the wait with their own error.
//
// The sentence is written to the log as the wait passes, and not only as the
// reason this carrier ended: ending a worker that is still loading its
// models takes worker_exit_ms and a kill (endWorker), and what its set
// declared to the host for this start may pass in that time.
func (c *carrier) awaitReady() (*aiiosdk.ReadyReport, error) {
	if left := c.limits.Ready - time.Since(c.began); left > 0 {
		select {
		case m := <-c.ready:
			return readinessReport(m.Ready, c.limits.WarmProbe)
		case e := <-c.fault:
			return nil, e
		case <-time.After(left):
		}
	}
	startupPhase("worker-readiness-not-reported", 0, 0)
	err := fmt.Errorf("the worker did not report ready within %d ms of this carrier's start, the time the limits table gives it (ready_ms); the worker was started %d ms into it", c.limits.Ready.Milliseconds(), c.workerStarted.Milliseconds())
	say(err)
	return nil, err
}

// endWorker waits for the worker's process to end, its input closed. The
// worker ends by itself inside retire_ms and says why when it cannot; this
// carrier waits worker_exit_ms, which the table holds to that and the margin
// at least (limits.valid), and only then kills the worker's processes and
// waits worker_reap_ms to see them gone. A limit that passes is said with its
// member and its number, at once and in the reason this carrier ended: at
// once, because on Windows the kill ends the job this carrier is in, and
// nothing it would have said after that is written.
func (c *carrier) endWorker(kill func() error) error {
	select {
	case e := <-c.done:
		return e
	case <-time.After(c.limits.WorkerExit):
	}
	late := fmt.Errorf("worker required forced cleanup: it did not exit in %d ms after its input was closed, the time the limits table gives it (worker_exit_ms)", c.limits.WorkerExit.Milliseconds())
	say(late)
	killErr := kill()
	select {
	case e := <-c.done:
		return errors.Join(killErr, e, late)
	case <-time.After(c.limits.WorkerReap):
	}
	unseen := fmt.Errorf("worker reap unproven: its exit was not seen in %d ms after it was killed, the time the limits table gives that (worker_reap_ms)", c.limits.WorkerReap.Milliseconds())
	say(unseen)
	return errors.Join(killErr, late, unseen)
}

func run() (err error) {
	p := declaredPlugin()
	if os.Getenv(aiiosdk.DescribeEnv) == "1" {
		return p.ServeSession(nil)
	}
	startupPhase("carrier-enter", 0, 0)
	cmd, waits, err := workerCommand(os.Args[1:])
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr
	startupPhase("worker-command-bound", 0, 0)
	cleanup, err := inheritAudio(cmd)
	if err != nil {
		return err
	}
	defer cleanup()
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	c := newCarrier(in, out, func() { _ = os.Stdin.Close() })
	c.cmd, c.limits, c.began = cmd, waits, startupBegan
	startupPhase("worker-start-begin", 0, 0)
	if err = cmd.Start(); err != nil {
		return err
	}
	c.workerStarted = time.Since(c.began)
	startupPhase("worker-started-awaiting-warm-inference", 0, 0)
	// Drain stdout before Wait closes the pipe: the last terminal/admission
	// bytes are not allowed to disappear because the worker exited promptly.
	go func() { <-c.readDone; c.done <- cmd.Wait() }()
	var settleEvents func() error // owed only while the public lane outlives a private fault
	defer func() {
		c.fail(io.EOF)
		c.workers.Wait() // private pipe close wakes writer; watchdog observes stop
		_ = in.Close()
		err = errors.Join(err, c.endWorker(func() error { return killWorker(cmd) }))
		if settleEvents != nil {
			err = errors.Join(err, settleEvents()) // the worker has said all it will
		}
		_ = out.Close()
	}()
	c.startPrivate()
	go c.read()
	ready, err := c.awaitReady()
	if err != nil {
		return err
	}
	startupPhase("worker-readiness-validated", 0, 0)
	settle, forwarded := make(chan struct{}), make(chan error, 1)
	go func() { forwarded <- c.forward(settle, (*aiiosdk.Session).Emit) }()
	// The SDK's Unix reader owns a separate poller-registered wrapper around fd 0.
	// Closing os.Stdin on a worker fault does not necessarily wake that reader.
	// Keep process retirement under the carrier's control: a private fault ends
	// run even if the SDK reader remains blocked until this process exits.
	served := make(chan error, 1)
	go func() {
		if ready == nil {
			served <- p.ServeSession(c.admit) // deterministic process tests only
		} else {
			served <- p.ServeSessionReady("AII_VOICE_READY", *ready, c.admit)
		}
	}()
	select {
	case err = <-served:
		close(settle) // the lane has ended: the SDK takes no further event
	case err = <-c.fault:
		settleEvents = func() error {
			return c.handoff(settle, forwarded, (*aiiosdk.Session).Flush)
		}
	}
	select {
	case e := <-c.fault:
		if !errors.Is(e, io.EOF) {
			err = errors.Join(err, e)
		}
	default:
	}
	return err
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "aii-voice-t3:", err)
		os.Exit(1)
	}
}
