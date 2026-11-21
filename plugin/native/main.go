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
	"slices"
	"strings"
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
	if !registeredAccelerators(body.Identity.Backend, r.Accelerator) || (r.Models != 4 && r.Models != 5) || r.ProbeMS <= 0 {
		return nil, errors.New("invalid worker warm-inference readiness")
	}
	if int64(r.ProbeMS) > warmProbe.Milliseconds() {
		return nil, fmt.Errorf("invalid worker warm-inference readiness: the warm inference took %d ms, more than the %d ms the limits table gives it (warm_probe_ms)", r.ProbeMS, warmProbe.Milliseconds())
	}
	return &aiiosdk.ReadyReport{ModelsLoaded: r.Models, Accelerator: r.Accelerator, ProbeMS: r.ProbeMS}, nil
}

var acceleratorWords = []string{"cpu", "cuda", "directml", "vulkan", "metal", "coreml"}

func registeredAccelerators(backend, line string) bool {
	speaking, known := map[string]string{"native-common-cpu": "", "native-common-vulkan": "vulkan", "native-common-metal": "metal"}[backend]
	parts := strings.Split(line, "+")
	if !known || len(parts) == 0 || parts[0] != "cpu" {
		return false
	}
	next := 0
	has := map[string]bool{}
	for _, part := range parts {
		at := slices.Index(acceleratorWords, part)
		if at < next {
			return false
		}
		next, has[part] = at+1, true
	}
	if speaking == "" {
		return !has["vulkan"] && !has["metal"]
	}
	return has[speaking]
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
	readySeen bool
	dropped   int
	unread    error
	id        uint64
	settings  settingsLane
	snapshots chan snapshotQuery
	ctx       context.Context
	cancel    context.CancelFunc

	limits limits

	began         time.Time
	workerStarted time.Duration

	vocabulary sync.Mutex

	rule json.RawMessage
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
	written  bool
}

func newCarrier(in io.WriteCloser, out io.ReadCloser, interrupt func()) *carrier {
	ctx, cancel := context.WithCancel(context.Background())
	return &carrier{in: in, out: out, writes: make(chan privateRequest, 64),
		pending: make(map[uint64]*privatePending), stop: make(chan struct{}), interrupt: interrupt,
		events: make(chan json.RawMessage, 64), ready: make(chan workerMessage, 1),
		fault: make(chan error, 1), done: make(chan error, 1), readDone: make(chan struct{}),
		settings: settingsLane{wake: make(chan struct{}, 1)}, snapshots: make(chan snapshotQuery, 1), ctx: ctx, cancel: cancel, limits: defaultLimits,
		began: time.Now()}
}

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

		for _, p := range unsent {
			p.answer(nil, fmt.Errorf("worker request not sent: %w", err))
		}
		c.fault <- err
		_ = c.in.Close()

	})
}

func (c *carrier) startPrivate() {
	c.startSettings()
	c.startSnapshots()
	c.startLane()
}

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
			case c.snapshots <- *m.Snapshot:
			default:
				c.fail(errors.New("private snapshot capacity exhausted"))
			}
		} else if m.Settings != nil {
			if m.ID != 0 || m.Error != "" || len(m.Result)+len(m.Event)+len(m.Ready) != 0 || !m.Settings.valid() {
				c.fail(errors.New("invalid private settings request"))
				continue
			}
			c.settings.offer(*m.Settings)
		} else if len(m.Ready) != 0 {

			c.mu.Lock()
			seen := c.readySeen
			c.readySeen = true
			c.mu.Unlock()
			if seen {
				c.fail(errors.New("duplicate worker readiness"))
				continue
			}
			c.ready <- m
		} else if len(m.Event) != 0 {
			select {
			case c.events <- m.Event:
			default:

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
		case <-settle:
		default:
			c.fail(err)
		}
		return err
	}
}

func (c *carrier) handoff(settle chan<- struct{}, forwarded <-chan error, flush func(*aiiosdk.Session, context.Context) error) (err error) {
	bound := c.limits.LaneFlush
	ctx, cancel := context.WithTimeout(context.Background(), bound)
	defer cancel()
	late := func(what string) error {
		return fmt.Errorf("worker event delivery unproven: %s in %d ms, the time the limits table gives the host's lane at this carrier's end (lane_flush_ms)", what, bound.Milliseconds())
	}
	defer func() {
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
	case <-c.readDone:
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
		return nil
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
	if err := validateRecoveryArchive(control.Op, control.Args); err != nil {
		control.Answer(nil, err)
		return
	}
	if vocabularyOperation(control.Op) {
		c.vocabularyStore(control)
		return
	}
	if control.Op == "recording.list" || control.Op == "recording.delete" || control.Op == "recording.prune" || recoveryArchiveOperation(control.Op) {
		c.recordingStore(control)
		return
	}
	if registryOperation(control.Op) {
		c.registryControl(control)
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

	p := &privatePending{answer: answer, deadline: time.Now().Add(c.controlWait(op))}
	c.pending[id] = p
	select {
	case c.writes <- request:
		return nil
	default:
		delete(c.pending, id)
		return errors.New("worker request not sent: ordered queue full")
	}
}

func (c *carrier) controlWait(op string) time.Duration {
	if enrollmentOperation(op) || op == "recording.record" {
		return c.limits.storageOperation()
	}
	if op == aiiosdk.OpSessionPlaybackReport {
		return c.limits.playbackReport()
	}
	return c.limits.Control
}

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

func reapUnadopted(cmd *exec.Cmd, adoptErr error, reap time.Duration) error {
	reaped := make(chan error, 1)
	go func() { reaped <- cmd.Wait() }()
	select {
	case e := <-reaped:
		return errors.Join(adoptErr, e)
	case <-time.After(reap):
		return errors.Join(adoptErr, fmt.Errorf("worker reap unproven: the worker the carrier could not adopt did not exit in %d ms, the time the limits table gives that (worker_reap_ms)", reap.Milliseconds()))
	}
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
	if err = adoptWorker(cmd); err != nil {
		return reapUnadopted(cmd, err, waits.WorkerReap)
	}
	c.workerStarted = time.Since(c.began)
	startupPhase("worker-started-awaiting-warm-inference", 0, 0)

	go func() { <-c.readDone; c.done <- cmd.Wait() }()
	var settleEvents func() error
	defer func() {
		c.fail(io.EOF)
		c.workers.Wait()
		_ = in.Close()
		err = errors.Join(err, c.endWorker(func() error { return killWorker(cmd) }))
		if settleEvents != nil {
			err = errors.Join(err, settleEvents())
		}
		c.interrupt()
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

	served := make(chan error, 1)
	go func() {
		if ready == nil {
			served <- p.ServeSession(c.admit)
		} else {
			served <- p.ServeSessionReady("AII_VOICE_READY", *ready, c.admit)
		}
	}()
	select {
	case err = <-served:
		close(settle)
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
