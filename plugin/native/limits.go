package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

// THE TIME LIMITS BETWEEN THE WORKER, THIS CARRIER AND THE HOST, IN ONE
// PLACE. They were numbers typed where they were used: 1.5 s for anything
// asked of the host, a durable write as much as a read; 2 s in the worker
// for the carrier's answer; 45 s here for a speaker operation, beside
// reads of 10 s each that could sum past it. On a disk that was slow for a
// moment the plugin gave up: a session refused at its opening, a speaker
// reported unavailable, a profile not stored. And the limits did not nest:
// an outer one could pass while everything inside it was still within its
// own.
//
// The numbers below are stated, in the signed runtime profile or by their
// defaults here: three for the host's storage, one for a session's end, one for a reply's
// wait for its settings, three for an abort, a capture's close and a
// session's open, one for when an opening says it is waiting, one for a
// control's answer, one each for a write of audio and a write of a line to
// this carrier, one for the worker's readiness, two for the worker's own
// end and a capture's last frames, three for this carrier's end, and nine
// for the engine the worker runs. Every other limit on this road is computed
// from them, so an outer limit always covers what is inside it, and the
// worker is handed the same table at its start.
type limits struct {
	// HostRead is how long one read the host answers may take: a page of
	// a private file, a session's settings.
	HostRead time.Duration
	// HostWrite is how long one durable write or publish the host answers
	// may take. A write is a file synced, a rename, a directory synced and
	// a record synced; it is not a read and does not get a read's time.
	HostWrite time.Duration
	// StorageWait is how long the plugin waits in all for the host's
	// storage before it says the storage is late: a session's opening, or
	// one whole read of a file.
	StorageWait time.Duration
	// DrainIdle is how long a session that is ending may go with nothing
	// moving before the worker calls it stalled: no audio delivered, no event
	// numbered, no storage, model call or write of audio in flight. Such work
	// has its own limit and holds this one open (drain_hold.h); of the
	// table's members only InputTail is inside it. It was 15 s typed.
	DrainIdle time.Duration
	// ReplySettings is how long a reply's first segment waits for the
	// settings in force, which the worker asks for as each reply begins so
	// that a voice the operator saved is the next reply's. It is a
	// listener's wait and not storage's: at its end the reply is spoken in
	// the voice already in force, and the answer serves the reply after.
	ReplySettings time.Duration
	// Abort is how long an aborted session's core has to retire, counted
	// from the first abort; CaptureClose how long an enrollment capture's
	// close has, its preparation included; SessionOpen how long a session's
	// open has to return, a speaking model's load included. At each the
	// worker says what did not come; an open or an abort that still does
	// not return ends the worker, which is what restarts a model that is
	// stuck. They were 5 s and 45 s typed in the worker, and no limit at all
	// for the open.
	Abort        time.Duration
	CaptureClose time.Duration
	SessionOpen  time.Duration
	// OpeningNotice is how long a session may be opening before the worker
	// says, in its log and its status, what the opening is waiting for.
	OpeningNotice time.Duration
	// Control is how long the worker has to answer a control that uses no
	// storage. AudioWrite is how long the worker gives one write of audio
	// to the host's pipe before it calls the pipe stalled and retires. They
	// were two seconds typed here and three typed in the worker, and they
	// did not nest: the worker holds a playback report until the audio
	// write it counts has ended, so a report held behind a stalled pipe
	// failed this carrier at two seconds, before the worker's own three
	// could say that the pipe had stalled (playbackReport).
	Control    time.Duration
	AudioWrite time.Duration
	// Ready is how long this carrier's worker has to report ready, its
	// models loaded and one warm inference run, counted from this carrier's
	// own start and not from the worker's (awaitReady): the check of the
	// runtime's files and the worker's spawn are inside it. The worker is
	// not handed it: only this carrier waits by it.
	Ready time.Duration
	// ControlWrite is how long the worker gives one write of a line to this
	// carrier before it calls the control channel stalled and retires. It
	// was three seconds typed in the worker's pipe. Nothing here nests with
	// it: this carrier's one reader takes the worker's lines as they come
	// and waits on nothing else (read), so such a write stalls only when
	// this carrier is not running at all.
	ControlWrite time.Duration
	// Retire is how long the worker has to end once it has begun to: its
	// input has ended, or it has failed. Past it the worker says what it
	// was still waiting for and ends with status 72. CaptureTail is how
	// long, once an enrollment capture has been told the sample it ends at,
	// the worker waits for the frames up to that sample; the host sends
	// them, and nothing here is inside or outside that wait. They were five
	// seconds and two typed in the worker.
	Retire      time.Duration
	CaptureTail time.Duration
	// WorkerExit is how long this carrier waits at its end for its worker's
	// process to exit, its input closed, before it kills the worker's
	// processes. It is the outer of Retire: valid refuses a table in which
	// it is not Retire and the margin at least, so a worker that is ending
	// inside its own time is never killed. WorkerReap is how long a killed
	// worker's exit is then waited for, and LaneFlush how long the host's
	// lane has, after that, to take the worker's last events and confirm
	// them written; the operating system and the host answer those two, and
	// they nest in nothing here. They were five seconds, five and two typed
	// in run, the first beside the worker's own five.
	WorkerExit time.Duration
	WorkerReap time.Duration
	LaneFlush  time.Duration
	// THE ENGINE'S OWN WAITS, which the worker hands on to where each is
	// waited by. They were numbers typed in the session core, the speaker
	// attribution and the model owner.
	//
	// ModelCall is how long one synchronous model inference may take: the
	// session's watchdog ends the session at it. It is not inside
	// SessionOpen, whose model load the watchdog does not watch. InputTail
	// is how long the frames of a conversation up to the sample it was told
	// it ends at are waited for; a drain's idle limit is its outer, and valid
	// refuses a table in which DrainIdle is not this and the margin at
	// least. An enrollment capture's frames are the worker's and wait by
	// CaptureTail.
	ModelCall time.Duration
	InputTail time.Duration
	// OutputTake is how long synthesized audio waits for room in the
	// session's bounded queue, which the worker empties by writing it to the
	// host. It is the outer of AudioWrite: the worker cannot take more while
	// a write is in flight, so valid refuses a table in which this is not
	// AudioWrite and the margin at least, and a pipe the host has stopped
	// taking is said by the pipe's own deadline, which sees it.
	OutputTake time.Duration
	// SpeakerMatch is how long a final waits for its speaker before the wait
	// is declared over. It is a listener's wait and the outer of nothing: the
	// match still running is not ended by it, and one that comes later is not
	// used.
	SpeakerMatch time.Duration
	// WarmProbe is how long the warm inference of a worker's start may take.
	// The worker's library fails the start past it, and this carrier refuses
	// a report of one that took longer (readinessReport): one member, so the
	// two cannot differ. It is inside Ready, by the margin (valid).
	WarmProbe time.Duration
	// EndpointDecision is how long, at the point where a pause would end a
	// turn, the engine waits for the endpoint model's verdict on that pause,
	// counted from when it asked. It is a listener's wait: past it the turn
	// ends by silence alone, nothing fails and the worker's log says so.
	// EndpointRetire is how long each such question still unanswered is
	// waited for when a conversation's input ends. The question is a model
	// call, so this is the outer of ModelCall: valid refuses a table in
	// which it is not ModelCall and the margin at least, and the call's own
	// limit speaks first. They were one second and fifteen typed in the
	// endpoint's gate, the second inside the thirty a model call had.
	EndpointDecision time.Duration
	EndpointRetire   time.Duration
	// SeparationMin and SeparationMax bound the budget one separation of
	// competing talkers has: five times the separated audio's length, and
	// between these two. Past its budget the separation is given up and the
	// turn keeps what was heard unseparated. A separation runs inside one
	// model call, so ModelCall is the outer of SeparationMax: valid refuses a
	// table in which it is not that and the margin at least. They were four
	// seconds and twenty-five typed in the recognizer, held under a typed
	// thirty.
	SeparationMin time.Duration
	SeparationMax time.Duration
}

// defaultLimits hold where the profile states none. CHOSEN, NOT MEASURED:
// a timed run on a slow disk under load is owed and will set them. They
// are chosen so that a control that uses storage (storageOperation, 77 s)
// ends inside the host's allowance for one invocation of a plugin (90 s,
// the host's own compiled number), with thirteen seconds
// between them: the host is never left waiting on a carrier that has
// already given up, nor the carrier on a host that has. Ready is chosen
// too, and not measured: it sits five seconds inside the 180 s that each
// set of the package declares to the host as the allowance for its start
// (startup_ms), so that this
// carrier says what it waited for before that allowance has passed. It
// was 180 s, the same number, which left no time for that. This carrier is
// not told what its set declares, so the package's assembly holds the two
// together: it refuses a set whose stated ready_ms and hostAnswerMargin
// together pass that set's startup_ms (startup_covers_readiness in
// scripts/runtime_limits.py). One set's worker reported ready 11.1 s after
// its start on the processor it ran on, and 194.7 s after it under a
// processor emulator: that start is past this default
// and past the allowance. WorkerExit is Retire and the margin, and
// EndpointRetire is ModelCall and the margin; every other member of a
// worker's end and of the engine's is the number that was typed.
var defaultLimits = limits{HostRead: 5 * time.Second, HostWrite: 12 * time.Second, StorageWait: 12 * time.Second, DrainIdle: 15 * time.Second, ReplySettings: 150 * time.Millisecond,
	Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: 60 * time.Second, OpeningNotice: 1500 * time.Millisecond,
	Control: 2 * time.Second, AudioWrite: 3 * time.Second, Ready: 175 * time.Second, ControlWrite: 3 * time.Second,
	Retire: 5 * time.Second, CaptureTail: 2 * time.Second, WorkerExit: 5500 * time.Millisecond, WorkerReap: 5 * time.Second, LaneFlush: 2 * time.Second,
	ModelCall: 30 * time.Second, InputTail: 3 * time.Second, OutputTake: 15 * time.Second, SpeakerMatch: 15 * time.Second, WarmProbe: 40 * time.Second,
	EndpointDecision: 1000 * time.Millisecond, EndpointRetire: 30500 * time.Millisecond, SeparationMin: 4 * time.Second, SeparationMax: 25 * time.Second}

// hostInvokeAllowance is how long the host waits for one invocation of a
// plugin where nothing else is said. It is the host's number, stated here
// so that a table whose storage control would outlast it is refused.
const hostInvokeAllowance = 90 * time.Second

// limitMargin is how much longer an outer waiter gives an inner one, so
// that the inner one always answers first and the outer never guesses.
const limitMargin = 500 * time.Millisecond

// hostAnswerMargin is what something this carrier says is given to reach the
// host inside the host's allowance for it: an answer inside an invocation's
// and, where the package is assembled, the word that its worker did not
// report ready inside what its set declares for a start. Like limitMargin it
// is a margin of the arithmetic, not a wait that anything is held to.
const hostAnswerMargin = 2 * time.Second

const (
	limitFloor   = 250 * time.Millisecond
	limitCeiling = 120 * time.Second
	// A reply's wait for its settings has a range of its own: it is time a
	// listener waits for the first sound. A turn's wait for the endpoint's
	// verdict is a listener's too, and has the same.
	replyFloor   = 10 * time.Millisecond
	replyCeiling = 2 * time.Second
	// The wait for a worker's readiness has a range of its own: a model
	// load is not a storage wait. Under a second is not a time in which
	// models load (and is what a number of seconds typed as milliseconds
	// comes to); an hour is the most the packaging scripts let a set
	// declare as its start's allowance to the host (startup_ms).
	readyFloor   = 1000 * time.Millisecond
	readyCeiling = 3600 * time.Second
)

// profileLimits is the "limits" member of voice-runtime.json, in milliseconds. A member that is
// absent takes its default; one this carrier does not know is refused (UnmarshalJSON, at this file's end).
type profileLimits struct {
	HostReadMS     *int64 `json:"host_read_ms,omitempty"`
	HostWriteMS    *int64 `json:"host_write_ms,omitempty"`
	StorageWaitMS  *int64 `json:"storage_wait_ms,omitempty"`
	DrainIdleMS    *int64 `json:"drain_idle_ms,omitempty"`
	ReplyMS        *int64 `json:"reply_settings_ms,omitempty"`
	AbortMS        *int64 `json:"abort_ms,omitempty"`
	CaptureMS      *int64 `json:"capture_close_ms,omitempty"`
	OpenMS         *int64 `json:"session_open_ms,omitempty"`
	NoticeMS       *int64 `json:"opening_notice_ms,omitempty"`
	ControlMS      *int64 `json:"control_ms,omitempty"`
	AudioWriteMS   *int64 `json:"audio_write_ms,omitempty"`
	ReadyMS        *int64 `json:"ready_ms,omitempty"`
	ControlWriteMS *int64 `json:"control_write_ms,omitempty"`
	RetireMS       *int64 `json:"retire_ms,omitempty"`
	CaptureTailMS  *int64 `json:"capture_tail_ms,omitempty"`
	WorkerExitMS   *int64 `json:"worker_exit_ms,omitempty"`
	WorkerReapMS   *int64 `json:"worker_reap_ms,omitempty"`
	LaneFlushMS    *int64 `json:"lane_flush_ms,omitempty"`
	ModelCallMS    *int64 `json:"model_call_ms,omitempty"`
	InputTailMS    *int64 `json:"input_tail_ms,omitempty"`
	OutputTakeMS   *int64 `json:"output_take_ms,omitempty"`
	SpeakerMatchMS *int64 `json:"speaker_match_ms,omitempty"`
	WarmProbeMS    *int64 `json:"warm_probe_ms,omitempty"`
	DecisionMS     *int64 `json:"endpoint_decision_ms,omitempty"`
	EndRetireMS    *int64 `json:"endpoint_retire_ms,omitempty"`
	SeparateMinMS  *int64 `json:"separation_min_ms,omitempty"`
	SeparateMaxMS  *int64 `json:"separation_max_ms,omitempty"`
}

// limitsFrom is the table a profile states, or the defaults. A table that
// is out of range or does not nest is refused: the carrier does not start
// on limits it would have to break.
func limitsFrom(p *profileLimits) (limits, error) {
	l := defaultLimits
	if p != nil {
		for _, m := range []struct {
			ms  *int64
			set *time.Duration
		}{{p.HostReadMS, &l.HostRead}, {p.HostWriteMS, &l.HostWrite}, {p.StorageWaitMS, &l.StorageWait}, {p.DrainIdleMS, &l.DrainIdle}, {p.ReplyMS, &l.ReplySettings},
			{p.AbortMS, &l.Abort}, {p.CaptureMS, &l.CaptureClose}, {p.OpenMS, &l.SessionOpen}, {p.NoticeMS, &l.OpeningNotice},
			{p.ControlMS, &l.Control}, {p.AudioWriteMS, &l.AudioWrite}, {p.ReadyMS, &l.Ready}, {p.ControlWriteMS, &l.ControlWrite},
			{p.RetireMS, &l.Retire}, {p.CaptureTailMS, &l.CaptureTail}, {p.WorkerExitMS, &l.WorkerExit}, {p.WorkerReapMS, &l.WorkerReap}, {p.LaneFlushMS, &l.LaneFlush},
			{p.ModelCallMS, &l.ModelCall}, {p.InputTailMS, &l.InputTail}, {p.OutputTakeMS, &l.OutputTake}, {p.SpeakerMatchMS, &l.SpeakerMatch}, {p.WarmProbeMS, &l.WarmProbe},
			{p.DecisionMS, &l.EndpointDecision}, {p.EndRetireMS, &l.EndpointRetire}, {p.SeparateMinMS, &l.SeparationMin}, {p.SeparateMaxMS, &l.SeparationMax}} {
			if m.ms != nil {
				*m.set = time.Duration(*m.ms) * time.Millisecond
			}
		}
	}
	return l, l.valid()
}

// valid says the stated limits are in range and the storage's three nest: a write is
// given at least a read's time, and the whole wait covers one write; that
// what this carrier waits for a control that uses storage, and for a playback
// report, ends inside the host's allowance; that this carrier waits for its
// worker's exit longer than the worker has to retire; and that the engine's
// waits nest with the ones around them.
func (l limits) valid() error {
	for name, d := range map[string]time.Duration{"host_read_ms": l.HostRead, "host_write_ms": l.HostWrite, "storage_wait_ms": l.StorageWait, "drain_idle_ms": l.DrainIdle,
		"abort_ms": l.Abort, "capture_close_ms": l.CaptureClose, "session_open_ms": l.SessionOpen,
		"opening_notice_ms": l.OpeningNotice, "control_ms": l.Control, "audio_write_ms": l.AudioWrite, "control_write_ms": l.ControlWrite,
		"retire_ms": l.Retire, "capture_tail_ms": l.CaptureTail, "worker_exit_ms": l.WorkerExit, "worker_reap_ms": l.WorkerReap, "lane_flush_ms": l.LaneFlush,
		"model_call_ms": l.ModelCall, "input_tail_ms": l.InputTail, "output_take_ms": l.OutputTake, "speaker_match_ms": l.SpeakerMatch, "warm_probe_ms": l.WarmProbe,
		"endpoint_retire_ms": l.EndpointRetire, "separation_min_ms": l.SeparationMin, "separation_max_ms": l.SeparationMax} {
		if d < limitFloor || d > limitCeiling {
			return fmt.Errorf("runtime limit %s is %d ms; it must be %d to %d", name, d.Milliseconds(), limitFloor.Milliseconds(), limitCeiling.Milliseconds())
		}
	}
	if l.ReplySettings < replyFloor || l.ReplySettings > replyCeiling {
		return fmt.Errorf("runtime limit reply_settings_ms is %d ms; it must be %d to %d", l.ReplySettings.Milliseconds(), replyFloor.Milliseconds(), replyCeiling.Milliseconds())
	}
	if l.EndpointDecision < replyFloor || l.EndpointDecision > replyCeiling {
		return fmt.Errorf("runtime limit endpoint_decision_ms is %d ms; it must be %d to %d", l.EndpointDecision.Milliseconds(), replyFloor.Milliseconds(), replyCeiling.Milliseconds())
	}
	if l.Ready < readyFloor || l.Ready > readyCeiling {
		return fmt.Errorf("runtime limit ready_ms is %d ms; it must be %d to %d", l.Ready.Milliseconds(), readyFloor.Milliseconds(), readyCeiling.Milliseconds())
	}
	if l.HostWrite < l.HostRead {
		return errors.New("runtime limits: a write must be given at least a read's time")
	}
	if l.StorageWait < l.HostWrite {
		return errors.New("runtime limits: the whole wait for storage must cover one write")
	}
	if l.storageOperation()+hostAnswerMargin > hostInvokeAllowance {
		return fmt.Errorf("runtime limits: a control that uses storage would be given %d ms, and the host waits %d ms for one invocation", l.storageOperation().Milliseconds(), hostInvokeAllowance.Milliseconds())
	}
	// The same allowance, the one number of the host's this file has. How
	// long the host waits for a session's control was not read; a wait this
	// carrier holds longer than an invocation's is refused as too long.
	if l.playbackReport()+hostAnswerMargin > hostInvokeAllowance {
		return fmt.Errorf("runtime limits: a playback report would be given %d ms, and the host waits %d ms for one invocation", l.playbackReport().Milliseconds(), hostInvokeAllowance.Milliseconds())
	}
	// The worker ends by itself inside Retire and says why when it cannot.
	// This carrier's wait is the outer one, by the margin and not by an equal
	// number, so its kill never comes while the worker is inside its own time.
	if l.WorkerExit < l.Retire+limitMargin {
		return fmt.Errorf("runtime limits: this carrier would wait %d ms for its worker to exit and the worker has %d ms to retire; the wait must be the longer by %d ms at least", l.WorkerExit.Milliseconds(), l.Retire.Milliseconds(), limitMargin.Milliseconds())
	}
	// The engine waits for its audio to be taken, and the worker takes it by
	// writing it to the host: the write is the inner of the two, so that a
	// pipe the host has stopped taking is said by the pipe's own deadline.
	if l.OutputTake < l.AudioWrite+limitMargin {
		return fmt.Errorf("runtime limits: the engine would wait %d ms for its audio to be taken (output_take_ms) and one write of that audio to the host is given %d ms (audio_write_ms); the wait must be the longer by %d ms at least", l.OutputTake.Milliseconds(), l.AudioWrite.Milliseconds(), limitMargin.Milliseconds())
	}
	// A drain that hears nothing is called stalled; a conversation's last
	// frames not coming is said as that, by the wait that is for them.
	if l.DrainIdle < l.InputTail+limitMargin {
		return fmt.Errorf("runtime limits: a drain may go %d ms with nothing moving (drain_idle_ms) and a conversation's last frames are waited for %d ms (input_tail_ms); the drain's must be the longer by %d ms at least", l.DrainIdle.Milliseconds(), l.InputTail.Milliseconds(), limitMargin.Milliseconds())
	}
	// The warm inference is part of a worker's becoming ready.
	if l.Ready < l.WarmProbe+limitMargin {
		return fmt.Errorf("runtime limits: a warm inference is given %d ms (warm_probe_ms) and the worker's readiness %d ms (ready_ms); readiness must be the longer by %d ms at least", l.WarmProbe.Milliseconds(), l.Ready.Milliseconds(), limitMargin.Milliseconds())
	}
	// A question the endpoint has not answered when the input ends is a model
	// call in flight: the watchdog ends it at its own limit and says so.
	if l.EndpointRetire < l.ModelCall+limitMargin {
		return fmt.Errorf("runtime limits: an endpoint question is waited for %d ms at the input's end (endpoint_retire_ms) and the model call it is has %d ms (model_call_ms); the wait must be the longer by %d ms at least", l.EndpointRetire.Milliseconds(), l.ModelCall.Milliseconds(), limitMargin.Milliseconds())
	}
	// A separation is given up at its budget, which is best effort; the
	// model call around it must not be ended first, which fails the session.
	if l.SeparationMax < l.SeparationMin {
		return fmt.Errorf("runtime limits: a separation's budget is at least %d ms (separation_min_ms) and at most %d ms (separation_max_ms); the most must not be the less", l.SeparationMin.Milliseconds(), l.SeparationMax.Milliseconds())
	}
	if l.ModelCall < l.SeparationMax+limitMargin {
		return fmt.Errorf("runtime limits: a separation may take %d ms (separation_max_ms) and the model call it runs in is given %d ms (model_call_ms); the call's must be the longer by %d ms at least", l.SeparationMax.Milliseconds(), l.ModelCall.Milliseconds(), limitMargin.Milliseconds())
	}
	return nil
}

// query is how long the host has for one query of the private files, by
// what the query does: "" reads a page, "stage" writes, "publish" makes
// what was staged the file.
func (l limits) query(action string) time.Duration {
	if action == "" {
		return l.HostRead
	}
	return l.HostWrite
}

// workerExchange is how long the worker waits for this carrier's answer
// to one such query: the host's time and the margin, so this carrier's
// answer, late or not, always comes first.
func (l limits) workerExchange(action string) time.Duration { return l.query(action) + limitMargin }

// opening is how long the host has for a session's settings and its
// correction list together.
func (l limits) opening() time.Duration { return l.StorageWait }

// workerOpening is how long the worker waits for them.
func (l limits) workerOpening() time.Duration { return l.opening() + limitMargin }

// wholeRead is the most one whole read of a private file may take in the
// worker: its pages and the page that reads it back.
func (l limits) wholeRead() time.Duration { return l.StorageWait + limitMargin }

// wholePublication is the most one publication may take in the worker:
// its stages, the publish, and the read that verifies it.
func (l limits) wholePublication() time.Duration {
	return 2*l.workerExchange("stage") + l.workerExchange("publish") + l.wholeRead()
}

// storageOperation is how long the worker has to answer a control that
// uses the private files (a speaker operation, a recording): the most its
// storage may take, twice a read and one publication, and what any control
// is given for its own answer. That last was two seconds typed here beside
// the control limit, and is the control limit. It is computed and not typed
// beside them: at this limit the carrier fails itself, which it must never
// do over an operation that is still inside its own limits.
func (l limits) storageOperation() time.Duration {
	return 2*l.wholeRead() + l.wholePublication() + l.Control
}

// playbackReport is how long the worker has to answer a playback report.
// The worker holds one until the audio write it counts has ended, and gives
// that write AudioWrite before it calls the pipe stalled; then it answers as
// it answers any control. This carrier waits for both, so the worker's own
// deadline always comes first and says what happened.
func (l limits) playbackReport() time.Duration { return l.AudioWrite + l.Control }

// ownStorage is how long one of this carrier's own operations on the private
// files has, none of which goes through the worker: the recordings' list, a
// recording's deletion, the pruning of abandoned stages, and the correction
// list's reads and changes. The longest asks the host for two reads and two
// durable writes (a correction is read, staged, published and read back), so
// each is given that. It was ten seconds typed at each, less than the one
// write the table allows. A table that valid accepts always gives a storage
// control more than this, so that rule keeps this inside the host's
// allowance too.
func (l limits) ownStorage() time.Duration { return 2*l.HostRead + 2*l.HostWrite }

// workerLimits is what the worker is handed at its start: the limits it
// waits by, in milliseconds. They are computed here and nowhere else; the
// worker checks that they are in range and nest, and does no arithmetic of
// its own on them, so the two sides cannot come to disagree.
type workerLimits struct {
	ExchangeReadMS     int64 `json:"exchange_read_ms"`
	ExchangeWriteMS    int64 `json:"exchange_write_ms"`
	OpeningMS          int64 `json:"opening_ms"`
	WholeReadMS        int64 `json:"whole_read_ms"`
	WholePublicationMS int64 `json:"whole_publication_ms"`
	DrainIdleMS        int64 `json:"drain_idle_ms"`
	ReplySettingsMS    int64 `json:"reply_settings_ms"`
	AbortMS            int64 `json:"abort_ms"`
	CaptureCloseMS     int64 `json:"capture_close_ms"`
	SessionOpenMS      int64 `json:"session_open_ms"`
	OpeningNoticeMS    int64 `json:"opening_notice_ms"`
	AudioWriteMS       int64 `json:"audio_write_ms"`
	ControlWriteMS     int64 `json:"control_write_ms"`
	RetireMS           int64 `json:"retire_ms"`
	CaptureTailMS      int64 `json:"capture_tail_ms"`
	ModelCallMS        int64 `json:"model_call_ms"`
	InputTailMS        int64 `json:"input_tail_ms"`
	OutputTakeMS       int64 `json:"output_take_ms"`
	SpeakerMatchMS     int64 `json:"speaker_match_ms"`
	WarmProbeMS        int64 `json:"warm_probe_ms"`
	EndpointDecisionMS int64 `json:"endpoint_decision_ms"`
	EndpointRetireMS   int64 `json:"endpoint_retire_ms"`
	SeparationMinMS    int64 `json:"separation_min_ms"`
	SeparationMaxMS    int64 `json:"separation_max_ms"`
}

func (l limits) forWorker() workerLimits {
	return workerLimits{
		ExchangeReadMS:     l.workerExchange("").Milliseconds(),
		ExchangeWriteMS:    l.workerExchange("stage").Milliseconds(),
		OpeningMS:          l.workerOpening().Milliseconds(),
		WholeReadMS:        l.wholeRead().Milliseconds(),
		WholePublicationMS: l.wholePublication().Milliseconds(),
		DrainIdleMS:        l.DrainIdle.Milliseconds(),
		ReplySettingsMS:    l.ReplySettings.Milliseconds(),
		AbortMS:            l.Abort.Milliseconds(),
		CaptureCloseMS:     l.CaptureClose.Milliseconds(),
		SessionOpenMS:      l.SessionOpen.Milliseconds(),
		OpeningNoticeMS:    l.OpeningNotice.Milliseconds(),
		AudioWriteMS:       l.AudioWrite.Milliseconds(),
		ControlWriteMS:     l.ControlWrite.Milliseconds(),
		RetireMS:           l.Retire.Milliseconds(),
		CaptureTailMS:      l.CaptureTail.Milliseconds(),
		ModelCallMS:        l.ModelCall.Milliseconds(),
		InputTailMS:        l.InputTail.Milliseconds(),
		OutputTakeMS:       l.OutputTake.Milliseconds(),
		SpeakerMatchMS:     l.SpeakerMatch.Milliseconds(),
		WarmProbeMS:        l.WarmProbe.Milliseconds(),
		EndpointDecisionMS: l.EndpointDecision.Milliseconds(),
		EndpointRetireMS:   l.EndpointRetire.Milliseconds(),
		SeparationMinMS:    l.SeparationMin.Milliseconds(),
		SeparationMaxMS:    l.SeparationMax.Milliseconds(),
	}
}

// limitsEnv names the one variable the worker reads its limits from. An
// inherited value is never passed on: the carrier states its own.
const limitsEnv = "AII_VOICE_LIMITS"

// environment is the worker's limits as the entry of its environment.
func (l limits) environment() string {
	b, _ := json.Marshal(l.forWorker()) // integers: cannot fail
	return limitsEnv + "=" + string(b)
}

// profileLimitNames are the members a profile may state: the names a table
// with every member stated is written with. They are read from the tags, so
// the list is typed once; one more member does not compile here until it is
// counted.
func profileLimitNames() map[string]json.RawMessage {
	v := int64(0)
	all, _ := json.Marshal(profileLimits{&v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v}) // integers: cannot fail
	var names map[string]json.RawMessage
	_ = json.Unmarshal(all, &names) // what was just written: cannot fail
	return names
}

// UnmarshalJSON reads the table strictly. The profile around it is read
// loosely, because it carries members that only the packaging scripts use.
// This table was read loosely with it: a limit that was misspelt was passed
// over, and one stated as null was read as not stated. Either way its
// default was taken, and a signed profile shipped a limit it did not state.
// Now a name is one of the members exactly, letter case included, as the
// scripts match it (scripts/runtime_limits.py), and its value is a whole
// number, or the profile is refused at the carrier's start. It stands at
// the end of the file because docs/NATIVE_WORKER_WIRE.md cites the lines
// above it by number.
func (p *profileLimits) UnmarshalJSON(data []byte) error {
	var stated map[string]json.RawMessage
	if err := json.Unmarshal(data, &stated); err != nil || stated == nil {
		return errors.New("runtime limits must be an object of whole milliseconds by name")
	}
	known := profileLimitNames()
	// The first in order of the names at fault, so that one table is always
	// refused for the same member. An empty name is a name too.
	var unknown, null *string
	for name, value := range stated {
		name := name
		if _, ok := known[name]; !ok {
			if unknown == nil || name < *unknown {
				unknown = &name
			}
		} else if string(value) == "null" && (null == nil || name < *null) {
			null = &name
		}
	}
	if unknown != nil {
		return fmt.Errorf("runtime limit %s is not one this carrier knows", *unknown)
	}
	if null != nil {
		return fmt.Errorf("runtime limit %s must be a whole number of milliseconds", *null)
	}
	// The same members under a type without this method: decoding into
	// profileLimits itself would call this method again, without end.
	type members profileLimits
	var m members
	if err := json.Unmarshal(data, &m); err != nil {
		return fmt.Errorf("runtime limits must be whole numbers of milliseconds: %w", err)
	}
	*p = profileLimits(m)
	return nil
}
