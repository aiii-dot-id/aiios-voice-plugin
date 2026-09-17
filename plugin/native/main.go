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
	ID       uint64          `json:"id"`
	Result   json.RawMessage `json:"result"`
	Error    string          `json:"error"`
	Event    json.RawMessage `json:"event"`
	Ready    json.RawMessage `json:"ready"`
	Settings *settingsQuery  `json:"settings_request,omitempty"`
	Snapshot *snapshotQuery  `json:"snapshot_request,omitempty"`
}

// Fixtures retain an explicitly unprofiled lane; every real worker must prove
// loaded models and measured inference before the SDK advertises readiness.
func readinessReport(raw json.RawMessage) (*aiiosdk.ReadyReport, error) {
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
	nativeCPU := r.Accelerator == "cpu" && body.Identity.Backend == "native-common-cpu"
	nativeVulkan := (r.Models == 4 || r.Models == 5) && r.Accelerator == "cpu_vulkan" && body.Identity.Backend == "native-common-vulkan"
	nativeMetal := (r.Models == 4 || r.Models == 5) && r.Accelerator == "cpu_metal" && body.Identity.Backend == "native-common-metal"
	if r.Models < 3 || r.Models > 5 || (r.Models == 5 && !nativeCPU && !nativeVulkan && !nativeMetal) || r.ProbeMS <= 0 || r.ProbeMS > 40000 || (!nativeCPU && !nativeVulkan && !nativeMetal && r.Accelerator != "metal" && r.Accelerator != "cuda" && r.Accelerator != "directml") {
		return nil, errors.New("invalid worker warm-inference readiness")
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
	id        uint64 // owned by SDK admission goroutine
	settings  chan settingsQuery
	snapshots chan snapshotQuery
	ctx       context.Context
	cancel    context.CancelFunc
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
}

func newCarrier(in io.WriteCloser, out io.ReadCloser, interrupt func()) *carrier {
	ctx, cancel := context.WithCancel(context.Background())
	return &carrier{in: in, out: out, writes: make(chan privateRequest, 64),
		pending: make(map[uint64]*privatePending), stop: make(chan struct{}), interrupt: interrupt,
		events: make(chan json.RawMessage, 64), ready: make(chan workerMessage, 1),
		fault: make(chan error, 1), done: make(chan error, 1), readDone: make(chan struct{}),
		settings: make(chan settingsQuery, 1), snapshots: make(chan snapshotQuery, 1), ctx: ctx, cancel: cancel}
}

func (c *carrier) fail(err error) {
	c.failOnce.Do(func() {
		c.cancel()
		c.mu.Lock()
		close(c.stop)
		pending := make([]*privatePending, 0, len(c.pending))
		for id, p := range c.pending {
			pending = append(pending, p)
			delete(c.pending, id)
		}
		c.mu.Unlock()
		for _, p := range pending {
			p.answer(nil, fmt.Errorf("worker admission unknown: %w", err))
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
	c.workers.Add(2)
	go func() {
		defer c.workers.Done()
		encoder := json.NewEncoder(c.in)
		for {
			select {
			case <-c.stop:
				return
			case req := <-c.writes:
				select {
				case <-c.stop:
					return
				default:
				}
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
			return
		}
		if m.Snapshot != nil {
			if m.Settings != nil || m.ID != 0 || m.Error != "" || len(m.Result)+len(m.Event)+len(m.Ready) != 0 || !m.Snapshot.valid() {
				c.fail(errors.New("invalid private snapshot request"))
				return
			}
			select {
			case c.snapshots <- *m.Snapshot:
			default:
				c.fail(errors.New("private snapshot capacity exhausted"))
				return
			}
		} else if m.Settings != nil {
			if m.ID != 0 || m.Error != "" || len(m.Result)+len(m.Event)+len(m.Ready) != 0 || !m.Settings.valid() {
				c.fail(errors.New("invalid private settings request"))
				return
			}
			select {
			case c.settings <- *m.Settings:
			default:
				c.fail(errors.New("private settings capacity exhausted"))
				return
			}
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
				return
			}
			c.ready <- m // capacity one, and this is the only send
		} else if len(m.Event) != 0 {
			select {
			case c.events <- m.Event:
			default:
				c.fail(errors.New("worker event queue full"))
				return
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
				return
			}
			var refusal error
			if m.Error != "" {
				refusal = errors.New(m.Error)
			}
			p.answer(m.Result, refusal)
		} else {
			c.fail(errors.New("unclassifiable worker message"))
			return
		}
	}
	if err := scanner.Err(); err != nil {
		c.fail(err)
	} else {
		c.fail(io.EOF)
	}
}

func (c *carrier) admit(control *aiiosdk.Control) {
	if err := validateEnrollment(control.Op, control.Args); err != nil {
		control.Answer(nil, err)
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
	p := &privatePending{answer: answer, deadline: time.Now().Add(2 * time.Second)}
	if enrollmentOperation(op) {
		p.deadline = time.Now().Add(45 * time.Second)
	} // bounded preparation, broker CAS and readback; never blocks admission
	c.pending[id] = p // register BEFORE enqueue: reply may precede our return
	select {
	case c.writes <- request:
		return nil // enqueued, not an admission verdict; only the worker answers
	default:
		delete(c.pending, id)
		return errors.New("worker request not sent: ordered queue full")
	}
}

func run() (err error) {
	p := declaredPlugin()
	if os.Getenv(aiiosdk.DescribeEnv) == "1" {
		return p.ServeSession(nil)
	}
	startupPhase("carrier-enter", 0, 0)
	cmd, err := workerCommand(os.Args[1:])
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
	c.cmd = cmd
	startupPhase("worker-start-begin", 0, 0)
	if err = cmd.Start(); err != nil {
		return err
	}
	startupPhase("worker-started-awaiting-warm-inference", 0, 0)
	// Drain stdout before Wait closes the pipe: the last terminal/admission
	// bytes are not allowed to disappear because the worker exited promptly.
	go func() { <-c.readDone; c.done <- cmd.Wait() }()
	defer func() {
		c.fail(io.EOF)
		c.workers.Wait() // private pipe close wakes writer; watchdog observes stop
		_ = in.Close()
		select {
		case e := <-c.done:
			err = errors.Join(err, e)
		case <-time.After(5 * time.Second):
			killErr := killWorker(cmd)
			select {
			case e := <-c.done:
				err = errors.Join(err, killErr, e, errors.New("worker required forced cleanup"))
			case <-time.After(5 * time.Second):
				err = errors.Join(err, killErr, errors.New("worker reap unproven"))
			}
		}
		_ = out.Close()
	}()
	c.startPrivate()
	go c.read()
	var ready *aiiosdk.ReadyReport
	select {
	case m := <-c.ready:
		ready, err = readinessReport(m.Ready)
		if err != nil {
			return err
		}
		startupPhase("worker-readiness-validated", 0, 0)
	case e := <-c.fault:
		return e
	case <-time.After(180 * time.Second):
		return errors.New("worker startup timeout")
	}
	eventDone := make(chan struct{})
	defer close(eventDone)
	go func() {
		for {
			select {
			case raw := <-c.events:
				c.mu.Lock()
				s := c.session
				c.mu.Unlock()
				if s == nil {
					c.fail(errors.New("event before first admission"))
					return
				}
				if e := s.Emit(raw); e != nil {
					c.fail(e)
					return
				}
			case <-eventDone:
				return
			}
		}
	}()
	if ready == nil {
		err = p.ServeSession(c.admit) // deterministic process tests, not model readiness
	} else {
		err = p.ServeSessionReady("AII_VOICE_READY", *ready, c.admit)
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
