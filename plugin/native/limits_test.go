package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
)

func ms(v int64) *int64 { return &v }

// THE LIMITS NEST BY CONSTRUCTION: every outer limit covers what is inside
// it, for the defaults and for any table a profile may state. The table
// below is the road from the host outward, each line an outer limit and
// the inner ones it must cover.
func TestEveryOuterLimitCoversWhatIsInsideIt(t *testing.T) {
	tables := []*profileLimits{nil,
		{HostReadMS: ms(250), HostWriteMS: ms(250), StorageWaitMS: ms(250)},
		{HostReadMS: ms(1500), HostWriteMS: ms(1500), StorageWaitMS: ms(2000)},
		{HostReadMS: ms(1000), HostWriteMS: ms(8000), StorageWaitMS: ms(14000)},
		{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(13000)}, // the most that ends inside the host's allowance
		// To the millisecond: a storage control of 87998 ms and two seconds
		// for its answer are 89998. One more of storage's wait is refused in
		// TestALimitsTableThatCannotHoldIsRefused. The scripts' copy of this
		// arithmetic is held to both (tests/test_runtime_limits.py).
		{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(14666)},
		// A control's answer and a write of audio, at the floor of each and
		// at the most a playback report may be given: 88000 ms and two
		// seconds for its answer are the host's 90000.
		{ControlMS: ms(250), AudioWriteMS: ms(250)},
		{ControlMS: ms(8000), AudioWriteMS: ms(80000), OutputTakeMS: ms(80500)},
		// A control's own answer is part of what a storage control is given:
		// with the default storage, 13000 ms of it is the last that ends
		// inside the host's allowance (88000 ms and the answer's margin).
		{ControlMS: ms(13000)},
		// The worker's readiness and a write of one of its lines, at the
		// ends of their ranges.
		{ReadyMS: ms(1000), WarmProbeMS: ms(500), ControlWriteMS: ms(250)},
		{ReadyMS: ms(3600000), ControlWriteMS: ms(120000)},
		// The worker's own time to retire and this carrier's wait for its
		// exit, which is the outer of the two by the margin: at the floor and
		// at the ceiling. And the three that nest in nothing, at the ends of
		// their range.
		{RetireMS: ms(250), WorkerExitMS: ms(750)},
		{RetireMS: ms(119500), WorkerExitMS: ms(120000)},
		{CaptureTailMS: ms(250), WorkerReapMS: ms(250), LaneFlushMS: ms(250)},
		{CaptureTailMS: ms(120000), WorkerReapMS: ms(120000), LaneFlushMS: ms(120000)},
		// The engine's five, at the floor and at the most each may be: the
		// wait for its audio to be taken is the outer of a write of that audio,
		// a drain's idle limit the outer of a conversation's last frames, and
		// the worker's readiness the outer of its warm inference, each by the
		// margin.
		// A model call is the outer of a separation and the inner of the wait
		// for an endpoint question, each by the margin, so the least it may be
		// is the floor of a separation and the margin, and the most the
		// ceiling of that wait less the margin.
		{ModelCallMS: ms(750), SeparateMinMS: ms(250), SeparateMaxMS: ms(250), InputTailMS: ms(250), OutputTakeMS: ms(3500), SpeakerMatchMS: ms(250), WarmProbeMS: ms(250)},
		{ModelCallMS: ms(119500), EndRetireMS: ms(120000), InputTailMS: ms(14500), OutputTakeMS: ms(120000), SpeakerMatchMS: ms(120000), WarmProbeMS: ms(120000)},
		{DrainIdleMS: ms(120000), InputTailMS: ms(119500)},
		{AudioWriteMS: ms(14500)},
		{ReadyMS: ms(40500)},
		// The endpoint's verdict at the ends of a listener's range; the wait
		// for one of its questions at the least a model call leaves it; and a
		// separation's two bounds equal, at the floor and at the most a model
		// call leaves them.
		{DecisionMS: ms(10)},
		{DecisionMS: ms(2000)},
		{EndRetireMS: ms(30500)},
		{SeparateMinMS: ms(250), SeparateMaxMS: ms(250)},
		{SeparateMinMS: ms(29500), SeparateMaxMS: ms(29500)},
	}
	for _, p := range tables {
		l, err := limitsFrom(p)
		if err != nil {
			t.Fatalf("a table in range that nests was refused: %+v: %v", p, err)
		}
		for _, c := range []struct {
			what         string
			outer, inner time.Duration
		}{
			{"the worker's wait for a read covers the host's read", l.workerExchange(""), l.query("")},
			{"the worker's wait for a stage covers the host's write", l.workerExchange("stage"), l.query("stage")},
			{"the worker's wait for a publish covers the host's write", l.workerExchange("publish"), l.query("publish")},
			{"a write is given at least a read's time", l.query("stage"), l.query("")},
			{"the worker's wait for its settings covers the host's", l.workerOpening(), l.opening()},
			{"a whole read covers one page and its readback at the host's pace", l.wholeRead(), l.StorageWait},
			{"a whole publication covers a stage, the publish and a read back at the host's pace", l.wholePublication(), l.query("stage") + l.query("publish") + l.StorageWait},
			{"a storage operation covers two reads and a publication", l.storageOperation(), 2*l.wholeRead() + l.wholePublication()},
			{"a storage operation covers its storage and what any control is given for its own answer", l.storageOperation(), 2*l.wholeRead() + l.wholePublication() + l.Control},
			{"the wait for a playback report covers the audio write the worker holds it behind, and a control's answer", l.playbackReport(), l.AudioWrite + l.Control},
			{"this carrier's wait for its worker's exit covers the worker's own time to retire, and the margin", l.WorkerExit, l.Retire + limitMargin},
			{"one of this carrier's own storage operations covers two reads and two writes at the host's pace", l.ownStorage(), 2*l.query("") + l.query("stage") + l.query("publish")},
			{"a storage control, which is held inside the host's allowance, is given more than any of this carrier's own storage operations", l.storageOperation(), l.ownStorage() + limitMargin},
			{"a staged upload is not taken for abandoned while the worker's publication of it may be in flight", abandonedStageAge, l.wholePublication() + limitMargin},
			{"nor while this carrier's own publication of it may be", abandonedStageAge, l.ownStorage() + limitMargin},
			{"the engine's wait for its audio to be taken covers one write of that audio to the host, and the margin", l.OutputTake, l.AudioWrite + limitMargin},
			{"a drain's idle limit covers the wait for a conversation's last frames, and the margin", l.DrainIdle, l.InputTail + limitMargin},
			{"the worker's readiness covers the warm inference of its start, and the margin", l.Ready, l.WarmProbe + limitMargin},
			{"the wait for an endpoint question at the input's end covers the model call it is, and the margin", l.EndpointRetire, l.ModelCall + limitMargin},
			{"a model call covers the longest separation inside it, and the margin", l.ModelCall, l.SeparationMax + limitMargin},
			{"the most a separation may take is not less than the least", l.SeparationMax, l.SeparationMin},
		} {
			if c.outer < c.inner {
				t.Errorf("%+v: %s: outer %v, inner %v", p, c.what, c.outer, c.inner)
			}
		}
		// Strictly more where a waiter must hear the answer before it gives up.
		if l.workerExchange("") <= l.query("") || l.workerOpening() <= l.opening() || l.storageOperation() <= 2*l.wholeRead()+l.wholePublication() || l.playbackReport() <= l.AudioWrite ||
			l.WorkerExit <= l.Retire || l.OutputTake <= l.AudioWrite || l.DrainIdle <= l.InputTail || l.Ready <= l.WarmProbe ||
			l.EndpointRetire <= l.ModelCall || l.ModelCall <= l.SeparationMax {
			t.Errorf("%+v: an outer waiter gives up at the same moment as the inner one answers", p)
		}
	}
}

// A WRITE IS NOT HELD TO A READ'S TIME. That was the fault: one 1.5 s for
// anything asked of the host.
func TestAWriteHasItsOwnLimit(t *testing.T) {
	l := defaultLimits
	if l.query("stage") != l.HostWrite || l.query("publish") != l.HostWrite || l.query("") != l.HostRead {
		t.Fatalf("each kind of query has its own limit: read %v stage %v publish %v", l.query(""), l.query("stage"), l.query("publish"))
	}
	if l.HostWrite <= l.HostRead {
		t.Fatalf("the default gives a durable write no more than a read: %v and %v", l.HostWrite, l.HostRead)
	}
}

// A TABLE THAT IS OUT OF RANGE OR DOES NOT NEST IS REFUSED, by name.
func TestALimitsTableThatCannotHoldIsRefused(t *testing.T) {
	for _, c := range []struct {
		p    profileLimits
		want string
	}{
		{profileLimits{HostReadMS: ms(0)}, "host_read_ms"},
		{profileLimits{HostReadMS: ms(-5)}, "host_read_ms"},
		{profileLimits{HostWriteMS: ms(249)}, "host_write_ms"},
		{profileLimits{StorageWaitMS: ms(120001)}, "storage_wait_ms"},
		{profileLimits{DrainIdleMS: ms(100)}, "drain_idle_ms"},
		{profileLimits{ReplyMS: ms(5)}, "reply_settings_ms"},
		{profileLimits{ReplyMS: ms(2001)}, "reply_settings_ms"},
		{profileLimits{AbortMS: ms(100)}, "abort_ms"},
		{profileLimits{CaptureMS: ms(120001)}, "capture_close_ms"},
		{profileLimits{OpenMS: ms(0)}, "session_open_ms"},
		{profileLimits{ControlMS: ms(249)}, "control_ms"},
		{profileLimits{AudioWriteMS: ms(120001)}, "audio_write_ms"},
		{profileLimits{ControlWriteMS: ms(249)}, "control_write_ms"},
		{profileLimits{ReadyMS: ms(999)}, "ready_ms"},
		{profileLimits{ReadyMS: ms(3600001)}, "ready_ms"},
		{profileLimits{HostReadMS: ms(6000), HostWriteMS: ms(5000), StorageWaitMS: ms(20000)}, "a write must be given at least a read's time"},
		{profileLimits{HostReadMS: ms(1000), HostWriteMS: ms(20000), StorageWaitMS: ms(15000)}, "must cover one write"},
		// A control that uses storage would outlast the host's wait for it.
		{profileLimits{HostReadMS: ms(5000), HostWriteMS: ms(30000), StorageWaitMS: ms(60000)}, "the host waits 90000 ms"},
		{profileLimits{HostReadMS: ms(5000), HostWriteMS: ms(14000), StorageWaitMS: ms(14000)}, "the host waits 90000 ms"},
		// One millisecond past the last table that ends inside it: 90001.
		{profileLimits{HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(14667)}, "the host waits 90000 ms"},
		// A playback report would be waited for longer than the host waits:
		// one millisecond past the most, and far past it.
		{profileLimits{ControlMS: ms(8000), AudioWriteMS: ms(80001)}, "a playback report would be given 88001 ms"},
		{profileLimits{ControlMS: ms(10000), AudioWriteMS: ms(80000)}, "a playback report would be given 90000 ms"},
		// A storage control is given the control's own answer too: one
		// millisecond more of it than the last that ends inside the host's.
		{profileLimits{ControlMS: ms(13001)}, "a control that uses storage would be given 88001 ms"},
		// The five of a worker's end, each out of its range.
		{profileLimits{RetireMS: ms(249)}, "retire_ms"},
		{profileLimits{CaptureTailMS: ms(120001)}, "capture_tail_ms"},
		{profileLimits{WorkerExitMS: ms(120001)}, "worker_exit_ms"},
		{profileLimits{WorkerReapMS: ms(249)}, "worker_reap_ms"},
		{profileLimits{LaneFlushMS: ms(0)}, "lane_flush_ms"},
		// This carrier's wait for its worker's exit is not the worker's own
		// time to retire and the margin: one millisecond short of it from
		// either side, and two equal numbers, which is what was typed.
		{profileLimits{WorkerExitMS: ms(5499)}, "the worker has 5000 ms to retire"},
		{profileLimits{RetireMS: ms(5001)}, "this carrier would wait 5500 ms for its worker to exit"},
		{profileLimits{RetireMS: ms(8000), WorkerExitMS: ms(8000)}, "the wait must be the longer by 500 ms at least"},
		// The engine's five, each out of its range.
		{profileLimits{ModelCallMS: ms(249)}, "model_call_ms"},
		{profileLimits{InputTailMS: ms(120001)}, "input_tail_ms"},
		{profileLimits{OutputTakeMS: ms(0)}, "output_take_ms"},
		{profileLimits{SpeakerMatchMS: ms(249)}, "speaker_match_ms"},
		{profileLimits{WarmProbeMS: ms(120001)}, "warm_probe_ms"},
		// A write of audio that the engine's wait for its audio to be taken
		// would cut short: one millisecond from either side, and two equal
		// numbers.
		{profileLimits{AudioWriteMS: ms(14501)}, "the engine would wait 15000 ms for its audio to be taken"},
		{profileLimits{OutputTakeMS: ms(3499)}, "one write of that audio to the host is given 3000 ms"},
		{profileLimits{AudioWriteMS: ms(20000), OutputTakeMS: ms(20000)}, "the wait must be the longer by 500 ms at least"},
		// A conversation's last frames waited for as long as a drain may be idle.
		{profileLimits{InputTailMS: ms(14501)}, "a drain may go 15000 ms with nothing moving"},
		{profileLimits{DrainIdleMS: ms(3499)}, "a conversation's last frames are waited for 3000 ms"},
		// A warm inference that the wait for readiness does not cover: the
		// least readiness alone, which the table took before the two were tied.
		{profileLimits{ReadyMS: ms(40499)}, "a warm inference is given 40000 ms (warm_probe_ms) and the worker's readiness 40499 ms"},
		{profileLimits{ReadyMS: ms(1000)}, "readiness must be the longer by 500 ms at least"},
		// The endpoint's two and the separation's two, each out of its range:
		// the verdict's is a listener's.
		{profileLimits{DecisionMS: ms(9)}, "endpoint_decision_ms"},
		{profileLimits{DecisionMS: ms(2001)}, "endpoint_decision_ms"},
		{profileLimits{EndRetireMS: ms(120001)}, "endpoint_retire_ms"},
		{profileLimits{SeparateMinMS: ms(249)}, "separation_min_ms"},
		{profileLimits{SeparateMaxMS: ms(120001)}, "separation_max_ms"},
		// The wait for an endpoint question that a model call's own limit
		// does not come before: one millisecond from either side, and the
		// fifteen seconds that were typed beside a model call's thirty.
		{profileLimits{EndRetireMS: ms(30499)}, "the model call it is has 30000 ms (model_call_ms)"},
		{profileLimits{ModelCallMS: ms(30001)}, "an endpoint question is waited for 30500 ms at the input's end"},
		{profileLimits{EndRetireMS: ms(15000)}, "the wait must be the longer by 500 ms at least"},
		// A separation that a model call's limit would end: one millisecond
		// from either side, and a least that is past the most.
		{profileLimits{SeparateMaxMS: ms(29501)}, "a separation may take 29501 ms (separation_max_ms)"},
		{profileLimits{ModelCallMS: ms(25499), EndRetireMS: ms(30500)}, "the model call it runs in is given 25499 ms (model_call_ms)"},
		{profileLimits{SeparateMinMS: ms(25001)}, "the most must not be the less"},
	} {
		if _, err := limitsFrom(&c.p); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%+v: wanted a refusal naming %q, got %v", c.p, c.want, err)
		}
	}
	// A member that is absent takes its default and nothing else moves.
	moved := defaultLimits
	moved.HostRead = 2 * time.Second
	if l, err := limitsFrom(&profileLimits{HostReadMS: ms(2000)}); err != nil || l != moved {
		t.Fatalf("an absent member takes its default: %+v %v", l, err)
	}
}

// THE WAITS OF A WORKER'S END HAVE THE NUMBERS THAT WERE TYPED, BUT FOR ONE.
// The worker had five seconds to retire and this carrier waited five for its
// exit: two equal numbers that began together, so the kill could come while
// the worker was still inside its own time. The worker keeps its five, and
// this carrier waits that and the margin.
func TestTheWaitsOfAWorkersEndAreTheTables(t *testing.T) {
	d := defaultLimits
	if d.Retire != 5*time.Second || d.CaptureTail != 2*time.Second || d.WorkerReap != 5*time.Second || d.LaneFlush != 2*time.Second {
		t.Fatalf("the defaults of a worker's end are not the numbers that were typed: %+v", d)
	}
	if d.WorkerExit != d.Retire+limitMargin || d.WorkerExit != 5500*time.Millisecond {
		t.Fatalf("this carrier waits %v for a worker that has %v to retire; it is that and the margin of %v", d.WorkerExit, d.Retire, limitMargin)
	}
	// The worker is handed its own two; the three this carrier waits by stay here.
	handed, err := json.Marshal(d.forWorker())
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]bool{`"retire_ms":5000`: true, `"capture_tail_ms":2000`: true, "worker_exit_ms": false, "worker_reap_ms": false, "lane_flush_ms": false} {
		if strings.Contains(string(handed), name) != want {
			t.Errorf("the table handed to the worker is %s: %s there is %v, wanted %v", handed, name, !want, want)
		}
	}
}

// THE WORKER'S DEFAULTS ARE WHAT THIS CARRIER'S DEFAULTS COMPUTE TO. The
// carrier owns the arithmetic and always hands the worker its limits; the
// worker's own defaults serve only a worker started with no carrier. They
// are written in its header, in another language, and nothing but this test
// keeps the two from drifting apart.
func TestTheWorkersDefaultsAreWhatTheCarriersDefaultsComputeTo(t *testing.T) {
	header, err := os.ReadFile(filepath.Join("..", "..", "runtime", "native", "session", "worker_limits.h"))
	if err != nil {
		t.Fatal(err)
	}
	stated := map[string]int64{}
	for _, m := range regexp.MustCompile(`std::chrono::milliseconds (\w+)\{(\d+)\};`).FindAllStringSubmatch(string(header), -1) {
		stated[m[1]], _ = strconv.ParseInt(m[2], 10, 64)
	}
	w := defaultLimits.forWorker()
	want := map[string]int64{"exchange_read": w.ExchangeReadMS, "exchange_write": w.ExchangeWriteMS, "opening": w.OpeningMS,
		"whole_read": w.WholeReadMS, "whole_publication": w.WholePublicationMS, "drain_idle": w.DrainIdleMS, "reply_settings": w.ReplySettingsMS,
		"abort": w.AbortMS, "capture_close": w.CaptureCloseMS, "session_open": w.SessionOpenMS, "opening_notice": w.OpeningNoticeMS,
		"audio_write": w.AudioWriteMS, "control_write": w.ControlWriteMS, "retire": w.RetireMS, "capture_tail": w.CaptureTailMS,
		"model_call": w.ModelCallMS, "input_tail": w.InputTailMS, "output_take": w.OutputTakeMS, "speaker_match": w.SpeakerMatchMS, "warm_probe": w.WarmProbeMS,
		"endpoint_decision": w.EndpointDecisionMS, "endpoint_retire": w.EndpointRetireMS, "separation_min": w.SeparationMinMS, "separation_max": w.SeparationMaxMS}
	if len(stated) != len(want) {
		t.Fatalf("the worker's header states %d limits, the carrier hands over %d: %v", len(stated), len(want), stated)
	}
	for name, ms := range want {
		if stated[name] != ms {
			t.Errorf("the worker's default %s is %d ms; the carrier's defaults compute %d", name, stated[name], ms)
		}
	}
	// And what is handed over is the one variable the worker reads, as
	// the object it parses.
	if got := defaultLimits.environment(); !strings.HasPrefix(got, limitsEnv+`={"exchange_read_ms":5500,`) {
		t.Fatalf("the worker's environment entry: %s", got)
	}
}

// THE ENGINE'S WAITS HAVE THE NUMBERS THAT WERE TYPED IN IT, AND THE WORKER
// IS HANDED ALL FIVE. A model call had 30 s and a conversation's last frames
// 3 s in the session's header; audio waiting to be taken had 15 s in the
// session; a final's wait for its speaker had 15 s in the attribution; and a
// warm inference had 40 s in the model owner, and again in this carrier.
func TestTheEnginesWaitsAreTheTables(t *testing.T) {
	d := defaultLimits
	if d.ModelCall != 30*time.Second || d.InputTail != 3*time.Second || d.OutputTake != 15*time.Second || d.SpeakerMatch != 15*time.Second || d.WarmProbe != 40*time.Second {
		t.Fatalf("the defaults of the engine's waits are not the numbers that were typed: %+v", d)
	}
	handed, err := json.Marshal(d.forWorker())
	if err != nil {
		t.Fatal(err)
	}
	for _, member := range []string{`"model_call_ms":30000`, `"input_tail_ms":3000`, `"output_take_ms":15000`, `"speaker_match_ms":15000`, `"warm_probe_ms":40000`} {
		if !strings.Contains(string(handed), member) {
			t.Errorf("the table handed to the worker is %s, without %s", handed, member)
		}
	}
}

// THE ENDPOINT'S WAITS AND A SEPARATION'S BUDGET ARE THE TABLE'S, AND THE
// WORKER IS HANDED ALL FOUR. The endpoint's verdict had a second and a
// separation four to twenty-five seconds; those are the defaults. A question
// of the endpoint's still unanswered at the input's end had fifteen seconds,
// inside the thirty of the model call it is: it has that call's time and the
// margin, so the call's own limit is the one that speaks.
func TestTheEndpointsWaitsAndASeparationsBudgetAreTheTables(t *testing.T) {
	d := defaultLimits
	if d.EndpointDecision != time.Second || d.SeparationMin != 4*time.Second || d.SeparationMax != 25*time.Second {
		t.Fatalf("the defaults of the endpoint's verdict and a separation's budget are not the numbers that were typed: %+v", d)
	}
	if d.EndpointRetire != d.ModelCall+limitMargin || d.EndpointRetire != 30500*time.Millisecond {
		t.Fatalf("an endpoint question is waited for %v at the input's end and the model call it is has %v; it is that and the margin of %v", d.EndpointRetire, d.ModelCall, limitMargin)
	}
	handed, err := json.Marshal(d.forWorker())
	if err != nil {
		t.Fatal(err)
	}
	for _, member := range []string{`"endpoint_decision_ms":1000`, `"endpoint_retire_ms":30500`, `"separation_min_ms":4000`, `"separation_max_ms":25000`} {
		if !strings.Contains(string(handed), member) {
			t.Errorf("the table handed to the worker is %s, without %s", handed, member)
		}
	}
	// And a table that states them is handed over as stated.
	l, err := limitsFrom(&profileLimits{DecisionMS: ms(700), EndRetireMS: ms(31000), SeparateMinMS: ms(3000), SeparateMaxMS: ms(20000)})
	if err != nil {
		t.Fatal(err)
	}
	if w := l.forWorker(); w.EndpointDecisionMS != 700 || w.EndpointRetireMS != 31000 || w.SeparationMinMS != 3000 || w.SeparationMaxMS != 20000 {
		t.Fatalf("the four as stated were handed over as %+v", w)
	}
}

// A CONTROL THAT USES STORAGE ENDS INSIDE THE HOST'S WAIT FOR IT, for the
// defaults and for every table the carrier accepts: the carrier's limit
// for such a control and two seconds for its answer to travel are no more
// than the host's allowance for one invocation.
func TestAStorageControlEndsInsideTheHostsAllowance(t *testing.T) {
	if got := defaultLimits.storageOperation(); got != 77*time.Second {
		t.Fatalf("the default storage control is given %v; the notes say 77 s", got)
	}
	for _, p := range []*profileLimits{nil, {HostReadMS: ms(250), HostWriteMS: ms(250), StorageWaitMS: ms(250)}, {HostReadMS: ms(5000), HostWriteMS: ms(13000), StorageWaitMS: ms(13000)}} {
		l, err := limitsFrom(p)
		if err != nil {
			t.Fatal(err)
		}
		if l.storageOperation()+hostAnswerMargin > hostInvokeAllowance {
			t.Errorf("%+v: a storage control of %v outlasts the host's %v", l, l.storageOperation(), hostInvokeAllowance)
		}
	}
}

// THE DEFAULT WAIT FOR READINESS ENDS INSIDE THE START A SET DECLARES. Each
// set of the package declares 180 seconds to the host as the allowance for
// its start (startup_ms). This carrier is not told that number: the
// package's assembly refuses a set whose ready_ms and hostAnswerMargin
// together pass what the set declares. The default is held here to the same
// rule against the number the sets declare, so that a default moved back up
// to it is seen on this side too. At 180 seconds the wait and the allowance
// were one number, with no time for the carrier to say what it had waited
// for.
func TestTheDefaultWaitForReadinessEndsInsideTheStartASetDeclares(t *testing.T) {
	const declaredStartup = 180 * time.Second
	if got := defaultLimits.Ready; got != 175*time.Second {
		t.Fatalf("the default wait for readiness is %v; the notes say 175 s", got)
	}
	if defaultLimits.Ready+hostAnswerMargin > declaredStartup {
		t.Fatalf("the default wait for readiness, %v, and %v for the carrier's word do not end inside the %v a set declares for its start", defaultLimits.Ready, hostAnswerMargin, declaredStartup)
	}
}

// A LIMIT THIS CARRIER DOES NOT KNOW IS REFUSED, NOT PASSED OVER. The
// profile is read loosely, and its "limits" member was read loosely with
// it: a limit that was misspelt was passed over, and one stated as null was
// read as not stated, and the carrier started on that limit's default with
// nothing said. Both are refused now, and a name must be one of the
// members exactly, letter case included. What was refused before (a value that is
// not a whole number, a table that is not an object) is still refused, and
// says what it is.
func TestALimitThisCarrierDoesNotKnowIsRefused(t *testing.T) {
	for _, c := range []struct{ table, want string }{
		{`{"host_reed_ms":2000}`, "runtime limit host_reed_ms is not one this carrier knows"},
		{`{"HOST_READ_MS":2000}`, "runtime limit HOST_READ_MS is not one this carrier knows"},
		{`{"host_read_ms":2000,"storage_wait":9000}`, "runtime limit storage_wait is not one this carrier knows"},
		{`{"host_read_ms":null}`, "runtime limit host_read_ms must be a whole number of milliseconds"},
		{`{"host_read_ms":"2000"}`, "runtime limits must be whole numbers of milliseconds"},
		{`{"host_read_ms":2000.5}`, "runtime limits must be whole numbers of milliseconds"},
		{`{"host_read_ms":2e3}`, "runtime limits must be whole numbers of milliseconds"},
		{`{"host_read_ms":true}`, "runtime limits must be whole numbers of milliseconds"},
		{`[]`, "runtime limits must be an object"},
		{`2000`, "runtime limits must be an object"},
		{`"host_read_ms"`, "runtime limits must be an object"},
	} {
		var p runtimeProfile
		err := json.Unmarshal([]byte(`{"limits":`+c.table+`}`), &p)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: wanted a refusal saying %q, got %v", c.table, c.want, err)
		}
	}
	// What is stated by its own name is still read, and what is left out
	// still takes its default.
	var p runtimeProfile
	if err := json.Unmarshal([]byte(`{"limits":{"host_read_ms":2000,"reply_settings_ms":300,"control_ms":1500,"audio_write_ms":4000,"ready_ms":240000,"control_write_ms":5000}}`), &p); err != nil {
		t.Fatal(err)
	}
	l, err := limitsFrom(p.Limits)
	if err != nil || l.HostRead != 2*time.Second || l.ReplySettings != 300*time.Millisecond || l.HostWrite != defaultLimits.HostWrite || l.SessionOpen != defaultLimits.SessionOpen ||
		l.Control != 1500*time.Millisecond || l.AudioWrite != 4*time.Second || l.Ready != 4*time.Minute || l.ControlWrite != 5*time.Second {
		t.Fatalf("a table stated by its own names: %+v %v", l, err)
	}
	// A profile with no table, and one whose table is empty, take the defaults.
	for _, raw := range []string{`{}`, `{"limits":{}}`} {
		var q runtimeProfile
		if err := json.Unmarshal([]byte(raw), &q); err != nil {
			t.Fatal(err)
		}
		if got, err := limitsFrom(q.Limits); err != nil || got != defaultLimits {
			t.Fatalf("%s: %+v %v", raw, got, err)
		}
	}
}

// A SIGNED PROFILE WHOSE LIMITS ARE NOT UNDERSTOOD DOES NOT START, on the
// road every packaged start takes. And the table the packaging scripts
// write, every member by name, starts on exactly what it states.
func TestAProfileWhoseLimitsAreNotUnderstoodDoesNotStart(t *testing.T) {
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	// start writes the native fixture's profile with this "limits" member,
	// as bytes a script could have written, binds a carrier to those bytes
	// and asks it for its worker.
	start := func(table string) (limits, error) {
		root, p := nativeRuntimeFixture(t, []byte("native worker"))
		raw, err := json.Marshal(p)
		if err != nil {
			t.Fatal(err)
		}
		var members map[string]json.RawMessage
		if err := json.Unmarshal(raw, &members); err != nil {
			t.Fatal(err)
		}
		members["limits"] = json.RawMessage(table)
		if raw, err = json.Marshal(members); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "voice-runtime.json"), raw, 0644); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256(raw)
		_, l, err := packagedWorker(filepath.Join(root, "carrier"), hex.EncodeToString(h[:]))
		return l, err
	}
	d := defaultLimits
	every, err := json.Marshal(profileLimits{HostReadMS: ms(d.HostRead.Milliseconds()), HostWriteMS: ms(d.HostWrite.Milliseconds()), StorageWaitMS: ms(d.StorageWait.Milliseconds()),
		DrainIdleMS: ms(d.DrainIdle.Milliseconds()), ReplyMS: ms(d.ReplySettings.Milliseconds()), AbortMS: ms(d.Abort.Milliseconds()),
		CaptureMS: ms(d.CaptureClose.Milliseconds()), OpenMS: ms(d.SessionOpen.Milliseconds()), NoticeMS: ms(d.OpeningNotice.Milliseconds()),
		ControlMS: ms(d.Control.Milliseconds()), AudioWriteMS: ms(d.AudioWrite.Milliseconds()),
		ReadyMS: ms(d.Ready.Milliseconds()), ControlWriteMS: ms(d.ControlWrite.Milliseconds()),
		RetireMS: ms(d.Retire.Milliseconds()), CaptureTailMS: ms(d.CaptureTail.Milliseconds()), WorkerExitMS: ms(d.WorkerExit.Milliseconds()),
		WorkerReapMS: ms(d.WorkerReap.Milliseconds()), LaneFlushMS: ms(d.LaneFlush.Milliseconds()),
		ModelCallMS: ms(d.ModelCall.Milliseconds()), InputTailMS: ms(d.InputTail.Milliseconds()), OutputTakeMS: ms(d.OutputTake.Milliseconds()),
		SpeakerMatchMS: ms(d.SpeakerMatch.Milliseconds()), WarmProbeMS: ms(d.WarmProbe.Milliseconds()),
		DecisionMS: ms(d.EndpointDecision.Milliseconds()), EndRetireMS: ms(d.EndpointRetire.Milliseconds()),
		SeparateMinMS: ms(d.SeparationMin.Milliseconds()), SeparateMaxMS: ms(d.SeparationMax.Milliseconds())})
	if err != nil {
		t.Fatal(err)
	}
	var named map[string]int64
	if err := json.Unmarshal(every, &named); err != nil || len(named) != len(profileLimitNames()) {
		t.Fatalf("the table the scripts write has every member the carrier knows (%d): %s %v", len(profileLimitNames()), every, err)
	}
	if l, err := start(string(every)); err != nil || l != defaultLimits {
		t.Fatalf("a profile that states every default: %+v %v", l, err)
	}
	if l, err := start(`{"host_read_ms":2000,"host_write_ms":8000,"storage_wait_ms":14000}`); err != nil || l.HostRead != 2*time.Second || l.HostWrite != 8*time.Second || l.StorageWait != 14*time.Second || l.Abort != d.Abort {
		t.Fatalf("a profile that states three: %+v %v", l, err)
	}
	for _, c := range []struct{ table, want string }{
		{`{"host_reed_ms":2000,"host_write_ms":8000,"storage_wait_ms":14000}`, "runtime limit host_reed_ms is not one this carrier knows"},
		{`{"host_read_ms":null}`, "runtime limit host_read_ms must be a whole number of milliseconds"},
		{`{"host_read_ms":100}`, "runtime limit host_read_ms is 100 ms"},
		{`{"host_read_ms":6000,"host_write_ms":5000,"storage_wait_ms":20000}`, "a write must be given at least a read's time"},
	} {
		if l, err := start(c.table); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: wanted the start refused saying %q, got %+v %v", c.table, c.want, l, err)
		}
	}
}
