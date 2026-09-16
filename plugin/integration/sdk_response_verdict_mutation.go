//go:build !wasm_unknown

package aiiosdk

// The resident session lane: full-duplex JSON-RPC 2.0
// over the same framed stdio as the ordinary lane, run as a DISTINCT
// dispatch mode by a voice_interface plugin — ServeSession instead of
// Serve. It exists because a resident speech session is nothing like a
// serialized invocation: control operations must be ADMITTED and
// answered while synthesis and playback run, and observations must flow
// back UNSOLICITED. The ordinary serialized lane and its behavior are
// untouched.
//
// The wire:
//   - control ops are requests in the invoke.call envelope (host -> guest,
//     id + method + params.operation), answered with the admission result;
//   - observations are JSON-RPC NOTIFICATIONS (guest -> host): a frame with
//     a "method" and NO "id" member, and NO response — never an id:null
//     answer, which is the ordinary lane's rule, not this one;
//   - the guest may still call the host (guest -> host request), answered
//     inline by the host;
//   - one reader goroutine and one serialized writer per endpoint.
//
// Admission is not completion. A control handler must RETURN QUICKLY with
// the admission result and start any long work (inference, audio) on its
// own goroutines; it must never block the admission path on a pipe, a
// lock held across inference, or audio capacity. Admission ORDER is
// preserved: a single admission goroutine calls the handler in the order
// the host sent the requests, so a synthesize followed by its cancel can
// never reorder into cancel-before-synthesize.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

// The resident session control operations, the host -> guest requests a
// SessionAdmit switches on (the engine builder's vocabulary). The wire keeps the invoke.call envelope; these are the
// values of params.operation.
const (
	OpSessionOpen         = "speech.session.open"
	OpSessionSynthesize   = "speech.session.synthesize"
	OpSessionCancelSynth  = "speech.session.cancel_synthesis"
	OpSessionStopPlayback = "speech.session.stop_playback"
	OpSessionFinishInput  = "speech.session.finish_input"
	OpSessionClose        = "speech.session.close"
	OpSessionStatus       = "speech.session.status"
	// OpSessionPlaybackReport carries the HOST-VALIDATED client playback
	// evidence for one output stream (aii-os docs/VOICE_PLAYBACK_RECEIPTS.md):
	// session_id, synthesis_id, a strict integer output_stream, a monotonic
	// rendered_samples count in the ENGINE's negotiated output clock, and a
	// strict boolean terminal. A report is client evidence, never acoustic
	// proof; terminal evidence is immutable (an exact retry is idempotent);
	// a drain close completes only when every generation has its terminal
	// report — a missing report is never a successful drain.
	OpSessionPlaybackReport = "speech.session.playback_report"
)

// The control contract every SessionAdmit must honor:
//
//   - ADMISSION IS NOT COMPLETION. A response establishes only that the
//     operation was accepted, never that its effect finished. open =
//     accepted into opening (readiness is a later event); synthesize =
//     the synthesis id is accepted (audio and the terminal synthesis
//     event come later); cancel_synthesis / stop_playback = the
//     rejection fence is installed (the compute or the playback retires
//     later); finish_input = the exact input cutoff is accepted (the
//     tail drains later); close = drain-or-abort accepted, new work
//     refused (resources released later); status = a bounded snapshot,
//     which must never query or wait on inference.
//
//   - finish_input carries an EXCLUSIVE final-sample cutoff (end_sample)
//     scoped to the input stream and its generation (Voice Core: audio
//     spans are start-inclusive/end-exclusive). The engine admits every
//     sample through the cutoff, refuses audio beyond it, and finalizes
//     only when the admitted tail is complete. Control can arrive before
//     the last audio packet on the endpoint plane; record the boundary
//     at once and await the identified frames within a bound. An
//     unexplained gap is a declared failure, NEVER permission to discard
//     words. drain-close is refused without a prior finish_input cutoff.
//
//   - EVENTS carry identity: a contiguous sequence, a unique event id,
//     and a registered type (Voice Core "Common laws"), scoped to the
//     activation, session and synthesis they concern. Session, synthesis
//     and stream ids are never reused. synthesis_cancelled resolves only
//     that synthesis and refuses its further chunks and id reuse.
//     synthesis_end is not playback_stop; a stop request is not measured
//     acoustic silence; a lost transport is not a closed session. Late
//     output past a cancellation fence must not revive a reply.
//
//   - INTERRUPTION is BOTH stop_playback and cancel_synthesis, admitted
//     independently and never one waiting on the other — and never
//     waiting behind a synthesize whose acknowledgement is delayed.
//
//   - INPUT COMPLETION is the engine's word, not a transcript: after the
//     admitted tail is processed and the recognizer has retired —
//     following every final transcript, and for silent input too — the
//     engine emits ONE input_finished event naming the input handle
//     (stream_id) and the exact cutoff (end_sample, processed_end_sample),
//     and status carries the same as input_completion (null until then,
//     stable across identical-cutoff retries; a changed cutoff is
//     refused). The host settles its eligible reply work and admits the
//     final synthesis only after that event; it never manufactures a
//     transcript to make a Finish resolve.
//
//   - PLAYBACK REPORTS (playback_report) are the host forwarding the
//     page's rendered-sample evidence per output stream: validated against
//     what was delivered, monotonic, terminal exactly once and then
//     immutable; the engine answers with playback_observation labelled
//     client evidence (playback_verified false). A drain close completes
//     only when every generation is terminally reported.

const (
	// sessionAdmitQueue bounds control requests awaiting admission; the
	// host is expected to keep this shallow, and a full queue is a
	// protocol fault, not backpressure to ride.
	sessionAdmitQueue = 64
	// sessionMaxPending bounds outstanding guest->host calls.
	sessionMaxPending = 64
)

// Session is the guest's handle to a resident session: emit observations
// to the host, and call the host. It is safe for concurrent use — the
// long work a control handler starts holds one of these and reports
// through it.
//
// Every outbound frame goes through ONE owned writer goroutine over a
// bounded queue. A caller hands the
// writer a frame under its context; the writer CLAIMS a frame before
// writing it. A caller that cancels first withdraws the frame — proven
// never sent, safe to retry. Once the writer has claimed it, only the
// writer knows: a write that began and failed, or a cancellation while
// the write is in progress, is an UNKNOWN outcome, never "not sent". A
// failed write faults the lane, and a fault interrupts the reader's
// pending read — the lane ends without waiting for another frame.
type Session struct {
	in      io.Reader // closed to interrupt a pending read when the lane faults
	out     io.Writer
	nextID  uint64
	pendMu  sync.Mutex
	pending map[uint64]chan sessionReply

	wOnce      sync.Once
	outQ       chan *outbound
	writerDone chan struct{}

	closeOnce sync.Once
	closed    chan struct{} // the termination signal: closed PROMPTLY when the lane ends

	faultMu        sync.Mutex
	fault          error // the first transport failure; ends the session
	intrOnce       sync.Once
	admissionSlots chan struct{}  // bounded unresolved admission results
	admissionWait  sync.WaitGroup // all deferred result waiters retire on closed
	responseWait   sync.WaitGroup // writer verdicts, also joined before success
}

// sessionOutQueue bounds frames awaiting the single writer.
const sessionOutQueue = 64

// errNotSent is the writer's verdict for a frame it never wrote.
var errNotSent = errors.New("not sent")

// outbound is one frame awaiting the writer, with its verdict.
type outbound struct {
	frame   []byte
	claimed atomic.Bool
	done    chan error // nil: written; errNotSent: withdrawn or dropped unwritten; else the write failed after it began
}

// end signals termination at once, so every pending HostCall is released
// and the host's side sees the lane close without waiting for workers.
func (s *Session) end() { s.closeOnce.Do(func() { close(s.closed) }) }

// setFault records the first transport failure and ends the lane.
func (s *Session) setFault(err error) {
	s.faultMu.Lock()
	if s.fault == nil {
		s.fault = err
	}
	s.faultMu.Unlock()
	s.end()
}

func (s *Session) faultErr() error {
	s.faultMu.Lock()
	defer s.faultMu.Unlock()
	return s.fault
}

// interruptRead releases a pending read: the input is closed when it can
// be. Stdin is made pollable before the lane opens (pollableStdin), so
// closing it wakes the blocked read; an os.Pipe or io.Pipe end behaves
// the same. A reader that cannot be closed is released at its next
// return, and the lane's owner does not wait for it.
func (s *Session) interruptRead() {
	s.intrOnce.Do(func() {
		if c, ok := s.in.(io.Closer); ok && s.in != nil {
			_ = c.Close()
		}
	})
}

type sessionReply struct {
	result json.RawMessage
	errObj json.RawMessage
}

// SessionAdmit admits ONE control operation and returns the admission
// result (any JSON-encodable value) or an error. It runs on a single
// goroutine in host-send order and must return quickly; start long work
// on goroutines that report through s.
type SessionAdmit func(s *Session, op string, args Object) (any, error)

// AdmissionResult is an engine's admission verdict, not completion of its work.
type AdmissionResult struct {
	Value any
	Err   error
}

// PendingAdmission is returned only after ORDERED enqueue to an external
// engine. The handler must not block awaiting that engine's acknowledgement.
// Send exactly one result on a buffered channel; the SDK owns the bounded
// wait and response correlation. Closing without a verdict faults the lane.
// The producer owns its own deadline and retirement on transport loss.
// This is a Go API extension, not a new public wire operation or success value.
type PendingAdmission <-chan AdmissionResult

// ServeSession runs the resident session lane on stdio and blocks until
// the host closes it (stdin EOF), the transport fails, or a fault ends
// it. A voice_interface plugin calls this instead of Serve.
func (p *Plugin) ServeSession(admit SessionAdmit) error {
	if describeAsked() {
		return p.writeDescriptors(os.Stdout)
	}
	return p.serveSession(pollableStdin(), os.Stdout, admit)
}

func (p *Plugin) serveSession(in io.Reader, out io.Writer, admit SessionAdmit) error {
	s := &Session{in: in, out: out, pending: make(map[uint64]chan sessionReply), closed: make(chan struct{}), admissionSlots: make(chan struct{}, sessionAdmitQueue)}
	s.startWriter()

	// The admission goroutine: one at a time, in arrival order. It hands
	// each response to the writer; a response the writer cannot deliver
	// faults the lane (the engine may have accepted an operation the host
	// never heard admitted — an unknown outcome, never silently
	// swallowed), and a lane that has ended stops admitting.
	admitCh := make(chan []byte, sessionAdmitQueue)
	admitDone := make(chan struct{})
	go func() {
		defer close(admitDone)
		for frame := range admitCh {
			if s.faultErr() != nil {
				continue // the lane is dead; drain without answering
			}
			if err := s.admitOne(admit, frame); err != nil {
				s.setFault(fmt.Errorf("aiiosdk: admission response not delivered: %w", err))
			}
		}
	}()

	// The reader, on its own goroutine: it never runs a handler inline,
	// and the lane's owner never waits on it past a fault — a fault
	// interrupts its read instead.
	readDone := make(chan error, 1)
	go func() { readDone <- s.readLoop(in, admitCh) }()
	var readErr error
	select {
	case readErr = <-readDone:
	case <-s.closed:
		// A fault ended the lane while the reader may be idle: interrupt
		// its read and do NOT wait for it — an interrupted read returns
		// at once on its own, and a reader that cannot be interrupted is
		// released at its next return. The lane's owner returns now.
		s.interruptRead()
		select {
		case readErr = <-readDone:
		default:
		}
	}
	// Termination is signalled, the admission goroutine drained with a
	// bound, and the writer released — never waited on unboundedly: a
	// write blocked on a host that stopped reading ends with the pipe.
	s.end()
	close(admitCh)
	select {
	case <-admitDone:
	case <-time.After(2 * time.Second):
	}
	s.admissionWait.Wait() // every deferred waiter selects closed; no engine wait
	select {
	case <-s.writerDone:
	case <-time.After(2 * time.Second):
	}
	s.responseWait.Wait() // no success while an accepted response is unaccounted
	if ferr := s.faultErr(); ferr != nil {
		return ferr
	}
	return readErr
}

// readLoop reads frames and routes each without doing handler work: a
// response to one of our host-calls wakes its caller; a request (id +
// method) is queued for ordered admission; a notification (method, no
// id) is delivered — the host does not send us notifications in this
// design, so an unexpected one is ignored rather than answered.
func (s *Session) readLoop(in io.Reader, admitCh chan<- []byte) error {
	for {
		if ferr := s.faultErr(); ferr != nil {
			return ferr
		}
		frame, err := ReadFrame(in, MaxControlFrameBytes)
		if err == io.EOF {
			return nil
		}
		if err != nil {
			if ferr := s.faultErr(); ferr != nil {
				return ferr // the read was interrupted by the fault
			}
			return fmt.Errorf("aiiosdk: session read: %w", err)
		}
		var m struct {
			ID     json.RawMessage `json:"id"`
			Method json.RawMessage `json:"method"`
			Result json.RawMessage `json:"result"`
			Error  json.RawMessage `json:"error"`
		}
		if json.Unmarshal(frame, &m) != nil {
			continue // a malformed frame is not a request we answer
		}
		switch {
		case len(m.Method) == 0 && len(m.ID) != 0:
			s.deliverReply(m.ID, sessionReply{result: m.Result, errObj: m.Error})
		case len(m.Method) != 0 && len(m.ID) != 0:
			// A control request, admitted in order. The queue is bounded
			// and the send never blocks the reader: a host that floods
			// controls without reading admissions has broken the lane,
			// and that is said, not ridden.
			select {
			case admitCh <- frame:
			default:
				return fmt.Errorf("aiiosdk: admission queue saturated (%d unanswered controls) — the lane is ended", sessionAdmitQueue)
			}
		default:
			// method + no id = a notification from the host (unused), or
			// an unclassifiable frame: ignored, never answered.
		}
	}
}

func (s *Session) admitOne(admit SessionAdmit, frame []byte) error {
	var req struct {
		ID     json.RawMessage `json:"id"`
		Params struct {
			Operation string          `json:"operation"`
			Arguments json.RawMessage `json:"arguments"`
		} `json:"params"`
	}
	_ = json.Unmarshal(frame, &req)
	select {
	case s.admissionSlots <- struct{}{}:
	default:
		return errors.New("too many unresolved admission results")
	}
	result, err := admit(s, req.Params.Operation, Object(req.Params.Arguments))
	if pending, ok := result.(PendingAdmission); ok && err == nil {
		if pending == nil {
			<-s.admissionSlots
			return errors.New("nil pending admission")
		}
		s.admissionWait.Add(1)
		go func() {
			defer s.admissionWait.Done()
			defer func() { <-s.admissionSlots }()
			select {
			case answer, open := <-pending:
				if !open {
					s.setFault(errors.New("engine admission ended without a verdict"))
					return
				}
				if err := s.respondAdmission(req.ID, answer.Value, answer.Err); err != nil {
					s.setFault(fmt.Errorf("admission response not delivered: %w", err))
				}
			case <-s.closed:
			}
		}()
		return nil
	}
	defer func() { <-s.admissionSlots }()
	return s.respondAdmission(req.ID, result, err)
}

func (s *Session) respondAdmission(id json.RawMessage, result any, err error) error {
	var response []byte
	if err != nil {
		response = sessionErrorFrame(id, err.Error())
	} else if payload, merr := json.Marshal(result); merr != nil {
		response = sessionErrorFrame(id, "admission result is not encodable: "+merr.Error())
	} else {
		response = sessionResultFrame(id, payload)
	}
	ob, qerr := s.enqueue(context.Background(), response)
	if qerr != nil {
		return qerr
	}
	// The writer faults on failed writes, but EOF can also retire it with a
	// response withdrawn, or race an enqueue after its last queue drain.
	// Own that verdict OFF the admission goroutine. Returning success merely
	// because enqueue succeeded discarded those outcomes (8d48264 review).
	s.responseWait.Add(1)
	go func() {
		defer s.responseWait.Done()
		var werr error
		select {
		case werr = <-ob.done:
		case <-s.writerDone:
			select {
			case werr = <-ob.done:
			default:
				werr = errNotSent
			}
		case <-s.closed:
			// A write in flight may finish after EOF. Wait for its actual
			// verdict, bounded, rather than calling delivered bytes unsent.
			select {
			case werr = <-ob.done:
			case <-s.writerDone:
				select {
				case werr = <-ob.done:
				default:
					werr = errNotSent
				}
			case <-time.After(2 * time.Second):
				werr = errors.New("writer outcome unknown after lane ended")
			}
		}
		if werr != nil {
			_ = werr // deliberate compiled mutation: lose the writer verdict
		}
	}()
	return nil
}

// startWriter starts the one owned writer, once.
func (s *Session) startWriter() {
	s.wOnce.Do(func() {
		if s.outQ == nil {
			s.outQ = make(chan *outbound, sessionOutQueue)
		}
		s.writerDone = make(chan struct{})
		go s.writer()
	})
}

// writer drains the queue to the pipe. It claims each frame before
// writing it; after the lane ends it withdraws whatever is still queued
// — those frames were provably never written.
func (s *Session) writer() {
	defer close(s.writerDone)
	for {
		select {
		case ob := <-s.outQ:
			s.write(ob)
		case <-s.closed:
			for {
				select {
				case ob := <-s.outQ:
					if ob.claimed.CompareAndSwap(false, true) {
						ob.done <- errNotSent
					}
				default:
					return
				}
			}
		}
	}
}

func (s *Session) write(ob *outbound) {
	if !ob.claimed.CompareAndSwap(false, true) {
		return // withdrawn by its caller
	}
	if s.faultErr() != nil {
		ob.done <- errNotSent // nothing is written after a fault: proven
		return
	}
	if err := WriteFrame(s.out, ob.frame, MaxControlFrameBytes); err != nil {
		s.setFault(fmt.Errorf("aiiosdk: transport write failed: %w", err))
		ob.done <- err // the write BEGAN: bytes may have reached the host
		return
	}
	ob.done <- nil
}

// enqueue hands a frame to the writer under ctx. errNotSent means the
// frame never entered the queue.
func (s *Session) enqueue(ctx context.Context, frame []byte) (*outbound, error) {
	s.startWriter()
	ob := &outbound{frame: frame, done: make(chan error, 1)}
	select {
	case s.outQ <- ob:
		return ob, nil
	case <-s.closed:
		return nil, errNotSent
	case <-ctx.Done():
		return nil, errNotSent
	}
}

// Emit sends an observation to the host as a JSON-RPC notification — a
// frame with a method and NO id, which receives no response. The event
// is the Voice Core event object (its own type, sequence and id live
// inside it); the host delivers it to the session observer. Emit returns
// once the writer holds the frame; a delivery failure faults the lane.
func (s *Session) Emit(event any) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("aiiosdk: encode event: %w", err)
	}
	frame := append([]byte(`{"jsonrpc":"2.0","method":"session.event","params":`), payload...)
	frame = append(frame, '}')
	if _, err := s.enqueue(context.Background(), frame); err != nil {
		return fmt.Errorf("aiiosdk: event not sent: the lane has ended")
	}
	return nil
}

// HostCall makes one guest->host request and waits for its reply. It is
// how a session reaches broker operations (voice.observe and the rest)
// from inside the resident lane; the host answers it on its own reader.
//
// Its failures are classified honestly. "not sent" is reserved for a
// frame that provably never left this process — never queued, withdrawn
// before the writer claimed it, or dropped unwritten after the lane
// ended — and only that is safe to retry after a rebind. Everything
// past the writer's claim is "outcome unknown": a write that failed
// after it began, a cancellation while the write was in progress, or a
// reply that never came. A caller must never replay a synthesis on an
// unknown outcome.
func (s *Session) HostCall(ctx context.Context, operation string, args any) (Object, error) {
	argsRaw, err := json.Marshal(args)
	if err != nil {
		return nil, fmt.Errorf("aiiosdk: encode hostcall args: %w", err)
	}
	s.pendMu.Lock()
	if len(s.pending) >= sessionMaxPending {
		s.pendMu.Unlock()
		return nil, fmt.Errorf("aiiosdk: too many outstanding host calls")
	}
	s.nextID++
	id := s.nextID
	ch := make(chan sessionReply, 1)
	s.pending[id] = ch
	s.pendMu.Unlock()
	defer func() {
		s.pendMu.Lock()
		delete(s.pending, id)
		s.pendMu.Unlock()
	}()

	frame := []byte(fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"invoke.call","params":{"operation":%q,"arguments":%s}}`, id, operation, argsRaw))
	ob, qerr := s.enqueue(ctx, frame)
	if qerr != nil {
		return nil, fmt.Errorf("aiiosdk: hostcall not sent: the lane ended, or the call was cancelled, before the frame was queued")
	}
	select {
	case werr := <-ob.done:
		if errors.Is(werr, errNotSent) {
			return nil, fmt.Errorf("aiiosdk: hostcall not sent: the lane ended before the frame was written")
		}
		if werr != nil {
			return nil, fmt.Errorf("aiiosdk: hostcall outcome unknown: the write failed after it began: %w", werr)
		}
	case <-ctx.Done():
		if ob.claimed.CompareAndSwap(false, true) {
			return nil, fmt.Errorf("aiiosdk: hostcall not sent: cancelled before the transport took the frame")
		}
		return nil, fmt.Errorf("aiiosdk: hostcall outcome unknown: cancelled while the transport was writing the frame")
	case <-s.closed:
		if ob.claimed.CompareAndSwap(false, true) {
			return nil, fmt.Errorf("aiiosdk: hostcall not sent: the lane ended before the frame was written")
		}
		return nil, fmt.Errorf("aiiosdk: hostcall outcome unknown: the lane ended while the transport was writing the frame")
	}
	select {
	case <-ctx.Done():
		return nil, fmt.Errorf("aiiosdk: hostcall outcome unknown: sent, no reply before cancellation")
	case <-s.closed:
		return nil, fmt.Errorf("aiiosdk: hostcall outcome unknown: sent, the session ended before the host answered")
	case reply := <-ch:
		if len(reply.errObj) != 0 {
			return nil, fmt.Errorf("aiiosdk: host refused: %s", reply.errObj)
		}
		return Object(reply.result), nil
	}
}

func (s *Session) deliverReply(idRaw json.RawMessage, reply sessionReply) {
	var id uint64
	if json.Unmarshal(idRaw, &id) != nil {
		return
	}
	s.pendMu.Lock()
	ch := s.pending[id]
	s.pendMu.Unlock()
	if ch != nil {
		select {
		case ch <- reply:
		default:
		}
	}
}

func sessionResultFrame(idRaw json.RawMessage, result json.RawMessage) []byte {
	if len(idRaw) == 0 {
		idRaw = json.RawMessage("null")
	}
	frame := append([]byte(`{"jsonrpc":"2.0","id":`), idRaw...)
	frame = append(frame, []byte(`,"result":`)...)
	frame = append(frame, result...)
	return append(frame, '}')
}

func sessionErrorFrame(idRaw json.RawMessage, message string) []byte {
	if len(idRaw) == 0 {
		idRaw = json.RawMessage("null")
	}
	msg, _ := json.Marshal(message)
	frame := append([]byte(`{"jsonrpc":"2.0","id":`), idRaw...)
	frame = append(frame, []byte(`,"error":{"code":-32000,"message":`)...)
	frame = append(frame, msg...)
	return append(frame, []byte(`}}`)...)
}

// SessionControls are the resident lane's eight control operations.
var SessionControls = []string{OpSessionOpen, OpSessionSynthesize, OpSessionCancelSynth, OpSessionStopPlayback, OpSessionFinishInput, OpSessionClose, OpSessionStatus, OpSessionPlaybackReport}

// DeclareSession registers the resident lane's controls as the plugin's
// declared operations: the packager's descriptor emission names them,
// and the host reads them as the session's controls — host lifecycle
// operations, never tools offered to the identity. On the ordinary
// serialized lane each refuses by name; they are admitted only through
// ServeSession's SessionAdmit.
func (p *Plugin) DeclareSession() *Plugin {
	for _, op := range SessionControls {
		p.Handle(op, func(Call) (any, error) {
			return nil, fmt.Errorf("aiiosdk: %s is a resident-session control — admitted on the session lane, never invoked", op)
		})
	}
	return p
}

// ServeSessionReady is ServeSession with real readiness: the ready line
// goes to stderr before the lane opens. Call it after the models are
// loaded, the accelerator is up and one bounded inference has run —
// never before. A variant that declares an accelerator profile must use
// this form: the host refuses a profiled child that merely spawned.
func (p *Plugin) ServeSessionReady(mark string, r ReadyReport, admit SessionAdmit) error {
	if describeAsked() {
		return p.writeDescriptors(os.Stdout)
	}
	fmt.Fprintln(os.Stderr, ReadyLine(mark, r))
	return p.serveSession(os.Stdin, os.Stdout, admit)
}

