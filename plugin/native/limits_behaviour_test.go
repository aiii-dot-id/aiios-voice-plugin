package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

// A QUERY OF THE SPEAKER FILES WAITS BY ITS KIND. One 1.5 s covered a page
// read, a stage and a publish alike; a durable write could not fit on a slow
// disk. Each query is now given the host's time for what it does.
func TestAQueryOfTheSpeakerFilesWaitsByItsKind(t *testing.T) {
	c, requests, _ := privateFixture(t)
	c.limits = limits{HostRead: 300 * time.Millisecond, HostWrite: 3 * time.Second, StorageWait: 3 * time.Second, DrainIdle: time.Second, ReplySettings: 150 * time.Millisecond, Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: time.Minute, OpeningNotice: 1500 * time.Millisecond}
	scan := bufio.NewScanner(requests)
	given := func(q snapshotQuery) time.Duration {
		t.Helper()
		var left time.Duration
		c.readSnapshot(q, func(ctx context.Context) (aiiosdk.Object, error) {
			deadline, ok := ctx.Deadline()
			if !ok {
				t.Fatal("a query of the host has no limit at all")
			}
			left = time.Until(deadline)
			return nil, errors.New("the host is not asked in this test")
		})
		if !scan.Scan() { // the reply to the worker, so the next one is not held behind it
			t.Fatal("no reply to the worker")
		}
		return left
	}
	upload, sha := strings.Repeat("a", 64), strings.Repeat("b", 64)
	read := given(snapshotQuery{settingsQuery: settingsQuery{1, "s"}})
	stage := given(snapshotQuery{settingsQuery: settingsQuery{2, "s"}, Action: "stage", Upload: upload, Data: base64.StdEncoding.EncodeToString([]byte("x"))})
	publish := given(snapshotQuery{settingsQuery: settingsQuery{3, "s"}, Action: "publish", Upload: upload, SHA: sha, Absent: true})
	if read > 300*time.Millisecond || read < 200*time.Millisecond {
		t.Errorf("a page read is given a read's time (300 ms): %v", read)
	}
	for name, d := range map[string]time.Duration{"stage": stage, "publish": publish} {
		if d > 3*time.Second || d < 2900*time.Millisecond {
			t.Errorf("a %s is given a write's time (3 s): %v", name, d)
		}
	}
}

// A SETTINGS FAILURE SAYS WHICH OF THREE IT WAS: the host gave no answer in
// the time, the host answered an error, or what it answered is not settings.
// One sentence used to cover all three, and a session refused at its opening
// could not say whether the host was slow or wrong. And the wait is the
// table's, not 1.5 s.
func TestASettingsFailureSaysWhichOfThreeItWas(t *testing.T) {
	for _, tc := range []struct {
		name string
		get  func(context.Context) (aiiosdk.Object, error)
		want string
	}{
		{"no answer in time", func(ctx context.Context) (aiiosdk.Object, error) { <-ctx.Done(); return nil, ctx.Err() }, settingsNoAnswer},
		{"the host's error", func(context.Context) (aiiosdk.Object, error) { return nil, errors.New("refused by the host") }, settingsHostError},
		{"not settings", func(context.Context) (aiiosdk.Object, error) {
			return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":[]}}`), nil
		}, settingsNotSettings},
		{"settings", func(context.Context) (aiiosdk.Object, error) {
			return aiiosdk.Object(`{"status":"succeeded","operation_result":{"values":{"stt_language":"en"}}}`), nil
		}, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, requests, _ := privateFixture(t)
			c.limits = limits{HostRead: 250 * time.Millisecond, HostWrite: 250 * time.Millisecond, StorageWait: 400 * time.Millisecond, DrainIdle: time.Second, Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: time.Minute, OpeningNotice: 1500 * time.Millisecond}
			began := time.Now()
			go c.readSettings(settingsQuery{1, "s"}, tc.get)
			scan := bufio.NewScanner(requests)
			if !scan.Scan() {
				t.Fatal("no settings outcome reached the worker")
			}
			var out privateRequest
			if err := json.Unmarshal(scan.Bytes(), &out); err != nil || out.Settings == nil {
				t.Fatalf("not a settings outcome: %s", scan.Bytes())
			}
			if out.Settings.Reason != tc.want || (tc.want == "") != (out.Settings.Error == "") {
				t.Fatalf("reason %q error %q, wanted reason %q", out.Settings.Reason, out.Settings.Error, tc.want)
			}
			if tc.want == "" && string(out.Settings.Values) != `{"stt_language":"en"}` {
				t.Fatalf("the settings were lost: %s", out.Settings.Values)
			}
			if took := time.Since(began); tc.want == settingsNoAnswer && (took < 350*time.Millisecond || took > 2*time.Second) {
				t.Fatalf("the wait for settings is the table's (400 ms), and it took %v", took)
			}
		})
	}
}

// A CONTROL THAT USES STORAGE IS GIVEN WHAT ITS STORAGE MAY TAKE, computed
// from the table, and one that does not is given the table's control limit.
// At this limit the carrier fails itself; it was 45 s typed beside reads of
// 10 s each that could sum past it.
//
// A PLAYBACK REPORT IS GIVEN THE AUDIO WRITE IT MAY BE HELD BEHIND, AND A
// CONTROL'S TIME. The worker holds a report until the audio write it counts
// has ended, and gives that write the table's audio_write before it calls
// the pipe stalled. The carrier gave every control two seconds, typed, and
// the worker gave a write three, typed: a report held behind a stalled pipe
// failed the carrier before the worker could say the pipe had stalled.
func TestAControlIsGivenWhatItsStorageMayTake(t *testing.T) {
	c, _, _ := privateFixture(t)
	for _, table := range []limits{defaultLimits, {HostRead: time.Second, HostWrite: 8 * time.Second, StorageWait: 14 * time.Second, DrainIdle: time.Minute, ReplySettings: 150 * time.Millisecond, Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: time.Minute, OpeningNotice: 1500 * time.Millisecond,
		Control: 1500 * time.Millisecond, AudioWrite: 7 * time.Second, Ready: 4 * time.Minute, ControlWrite: 5 * time.Second,
		Retire: 4 * time.Second, CaptureTail: 3 * time.Second, WorkerExit: 6 * time.Second, WorkerReap: 4 * time.Second, LaneFlush: 3 * time.Second,
		ModelCall: 20 * time.Second, InputTail: 2 * time.Second, OutputTake: 9 * time.Second, SpeakerMatch: 10 * time.Second, WarmProbe: 30 * time.Second}} {
		c.limits = table
		for _, op := range []string{"speaker.list", "speaker.enroll", "recording.record"} {
			if got := c.controlWait(op); got != table.storageOperation() || got <= 2*table.wholeRead()+table.wholePublication() {
				t.Errorf("%s under %+v: %v, wanted the storage's own total and more", op, table, got)
			}
			// What it is given beyond its storage is what any control is
			// given for its own answer, the table's and no longer two
			// seconds typed beside it.
			if got := c.controlWait(op); got != 2*table.wholeRead()+table.wholePublication()+table.Control {
				t.Errorf("%s under %+v: %v, wanted its storage and the table's control limit of %v", op, table, got, table.Control)
			}
		}
		for _, op := range []string{"stop_playback", "synthesize", "status", aiiosdk.OpSessionStopPlayback, aiiosdk.OpSessionSynthesize, aiiosdk.OpSessionCancelSynth,
			aiiosdk.OpSessionStatus, aiiosdk.OpSessionOpen, aiiosdk.OpSessionClose, aiiosdk.OpSessionFinishInput} {
			if got := c.controlWait(op); got != table.Control {
				t.Errorf("%s uses no storage and waits for no audio, and is given %v where the table's control limit is %v", op, got, table.Control)
			}
		}
		if got := c.controlWait(aiiosdk.OpSessionPlaybackReport); got != table.AudioWrite+table.Control || got <= table.AudioWrite {
			t.Errorf("a playback report under %+v is given %v: the worker may hold it for an audio write's %v and then answers as a control in %v", table, got, table.AudioWrite, table.Control)
		}
	}
	// The defaults: what every control but one waited before, and the one that now nests.
	c.limits = defaultLimits
	if plain, report := c.controlWait(aiiosdk.OpSessionStatus), c.controlWait(aiiosdk.OpSessionPlaybackReport); plain != 2*time.Second || report != 5*time.Second {
		t.Errorf("by default a control is given %v and a playback report %v; they are two seconds and five", plain, report)
	}
}

// THE WORKER IS HANDED THE CARRIER'S LIMITS AND NO OTHER. The signed profile
// states the table; the worker's environment carries what it computes to,
// once; a value inherited from outside is not passed on; and a profile whose
// table cannot hold stops the start.
func TestTheWorkerIsHandedTheProfilesLimits(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("AII_MODELS_DIR", t.TempDir())
	t.Setenv(limitsEnv, `{"exchange_read_ms":1}`) // from outside: never the worker's
	handed := func(env []string) []string {
		var got []string
		for _, entry := range env {
			if strings.HasPrefix(entry, limitsEnv+"=") {
				got = append(got, entry)
			}
		}
		return got
	}

	root, p := nativeRuntimeFixture(t, b)
	p.Limits = &profileLimits{HostReadMS: ms(1000), HostWriteMS: ms(4000), StorageWaitMS: ms(6000), DrainIdleMS: ms(9000), ReplyMS: ms(200), AbortMS: ms(3000), CaptureMS: ms(30000), OpenMS: ms(40000), NoticeMS: ms(700),
		ControlMS: ms(1500), AudioWriteMS: ms(4000), ReadyMS: ms(240000), ControlWriteMS: ms(5000),
		RetireMS: ms(4000), CaptureTailMS: ms(3000), WorkerExitMS: ms(6000), WorkerReapMS: ms(7000), LaneFlushMS: ms(900),
		ModelCallMS: ms(20000), InputTailMS: ms(2500), OutputTakeMS: ms(6000), SpeakerMatchMS: ms(8000), WarmProbeMS: ms(30000),
		DecisionMS: ms(700), EndRetireMS: ms(21000), SeparateMinMS: ms(3000), SeparateMaxMS: ms(19000)}
	cmd, l, err := packagedWorker(filepath.Join(root, "carrier"), writeRuntimeProfile(t, root, p))
	if err != nil {
		t.Fatal(err)
	}
	if l.HostRead != time.Second || l.HostWrite != 4*time.Second || l.StorageWait != 6*time.Second || l.DrainIdle != 9*time.Second || l.ReplySettings != 200*time.Millisecond ||
		l.Abort != 3*time.Second || l.CaptureClose != 30*time.Second || l.SessionOpen != 40*time.Second || l.OpeningNotice != 700*time.Millisecond ||
		l.Control != 1500*time.Millisecond || l.AudioWrite != 4*time.Second || l.Ready != 4*time.Minute || l.ControlWrite != 5*time.Second ||
		l.Retire != 4*time.Second || l.CaptureTail != 3*time.Second || l.WorkerExit != 6*time.Second || l.WorkerReap != 7*time.Second || l.LaneFlush != 900*time.Millisecond ||
		l.ModelCall != 20*time.Second || l.InputTail != 2500*time.Millisecond || l.OutputTake != 6*time.Second || l.SpeakerMatch != 8*time.Second || l.WarmProbe != 30*time.Second ||
		l.EndpointDecision != 700*time.Millisecond || l.EndpointRetire != 21*time.Second || l.SeparationMin != 3*time.Second || l.SeparationMax != 19*time.Second {
		t.Fatalf("the profile's table was not taken: %+v", l)
	}
	want := limitsEnv + `={"exchange_read_ms":1500,"exchange_write_ms":4500,"opening_ms":6500,"whole_read_ms":6500,"whole_publication_ms":20000,"drain_idle_ms":9000,"reply_settings_ms":200,"abort_ms":3000,"capture_close_ms":30000,"session_open_ms":40000,"opening_notice_ms":700,"audio_write_ms":4000,"control_write_ms":5000,"retire_ms":4000,"capture_tail_ms":3000,"model_call_ms":20000,"input_tail_ms":2500,"output_take_ms":6000,"speaker_match_ms":8000,"warm_probe_ms":30000,"endpoint_decision_ms":700,"endpoint_retire_ms":21000,"separation_min_ms":3000,"separation_max_ms":19000}`
	if got := handed(cmd.Env); len(got) != 1 || got[0] != want {
		t.Fatalf("the worker's limits:\n got %q\nwant %q", got, want)
	}

	// A profile that states none takes the defaults.
	root, p = nativeRuntimeFixture(t, b)
	cmd, l, err = packagedWorker(filepath.Join(root, "carrier"), writeRuntimeProfile(t, root, p))
	if err != nil || l != defaultLimits {
		t.Fatalf("a profile with no table takes the defaults: %+v %v", l, err)
	}
	if got := handed(cmd.Env); len(got) != 1 || got[0] != defaultLimits.environment() {
		t.Fatalf("the worker's default limits: %q", got)
	}

	// A worker named on the command line, in development, is handed the
	// defaults, once, and not what this process inherited.
	cmd, l, err = workerCommand([]string{self})
	if err != nil || l != defaultLimits {
		t.Fatalf("a development worker takes the defaults: %+v %v", l, err)
	}
	if got := handed(cmd.Env); len(got) != 1 || got[0] != defaultLimits.environment() {
		t.Fatalf("a development worker's limits: %q", got)
	}

	// A table that cannot hold stops the start.
	root, p = nativeRuntimeFixture(t, b)
	p.Limits = &profileLimits{HostReadMS: ms(5000), HostWriteMS: ms(1000)}
	if _, _, err := packagedWorker(filepath.Join(root, "carrier"), writeRuntimeProfile(t, root, p)); err == nil || !strings.Contains(err.Error(), "a write must be given at least a read's time") {
		t.Fatalf("a profile whose limits do not nest was started: %v", err)
	}
}

// A WORKER THAT DOES NOT REPORT READY IS WAITED FOR THE TABLE'S TIME, COUNTED
// FROM THE CARRIER'S START, AND THE CARRIER SAYS SO WITH THE NUMBER. Stated
// here as 300 ms for a carrier that has just begun, the wait ends 300 ms on
// and says how long the table gives, what it is counted from, which member
// states it and when the worker was started. For a carrier that began two
// seconds ago under a table that gives two and a half, half a second is
// left, not two and a half more: the time before the worker was started is
// inside the wait. Where that time is already all spent, the sentence is said
// at once. A worker that reports in time is taken at once, and a fault of the
// lane ends the wait with its own error.
func TestAWorkerThatDoesNotReportReadyIsWaitedForTheTablesTime(t *testing.T) {
	type outcome struct {
		ready *aiiosdk.ReadyReport
		err   error
		after time.Duration
	}
	awaited := func(c *carrier) outcome {
		t.Helper()
		began, done := time.Now(), make(chan outcome, 1)
		go func() {
			ready, err := c.awaitReady()
			done <- outcome{ready, err, time.Since(began)}
		}()
		select {
		case o := <-done:
			return o
		case <-time.After(10 * time.Second):
			t.Fatalf("the wait for readiness went on past ten seconds under a table that gives it %v", c.limits.Ready)
			return outcome{}
		}
	}
	c, _, _ := privateFixture(t)
	c.limits.Ready, c.began = 300*time.Millisecond, time.Now()
	o := awaited(c)
	if o.ready != nil || o.err == nil || o.err.Error() != "the worker did not report ready within 300 ms of this carrier's start, the time the limits table gives it (ready_ms); the worker was started 0 ms into it" {
		t.Fatalf("a worker that never reported ready: %+v %v", o.ready, o.err)
	}
	if o.after < 300*time.Millisecond || o.after > 5*time.Second {
		t.Fatalf("the table gives readiness 300 ms and the wait ended after %v", o.after)
	}

	// Two of the table's two and a half seconds went before the worker was
	// started: the wait is what is left of them.
	c, _, _ = privateFixture(t)
	c.limits.Ready, c.began, c.workerStarted = 2500*time.Millisecond, time.Now().Add(-2*time.Second), 1900*time.Millisecond
	o = awaited(c)
	if o.ready != nil || o.err == nil || o.err.Error() != "the worker did not report ready within 2500 ms of this carrier's start, the time the limits table gives it (ready_ms); the worker was started 1900 ms into it" {
		t.Fatalf("a worker that never reported ready, two seconds into its carrier's start: %+v %v", o.ready, o.err)
	}
	if o.after < 450*time.Millisecond || o.after > 1500*time.Millisecond {
		t.Fatalf("half a second of the table's time was left and the wait ended after %v: it is counted from the carrier's start, not from the worker's", o.after)
	}

	// All of it went before the worker was started: nothing is waited.
	c, _, _ = privateFixture(t)
	c.limits.Ready, c.began, c.workerStarted = time.Second, time.Now().Add(-5*time.Second), 4800*time.Millisecond
	o = awaited(c)
	if o.ready != nil || o.err == nil || o.err.Error() != "the worker did not report ready within 1000 ms of this carrier's start, the time the limits table gives it (ready_ms); the worker was started 4800 ms into it" {
		t.Fatalf("a worker started after its carrier's time for a start had gone: %+v %v", o.ready, o.err)
	}
	if o.after > 500*time.Millisecond {
		t.Fatalf("the time was spent before the worker was started, and the carrier still waited %v", o.after)
	}

	c, _, _ = privateFixture(t)
	c.limits.Ready = time.Hour
	c.ready <- workerMessage{Ready: json.RawMessage(`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":23}}`)}
	if o := awaited(c); o.err != nil || o.ready == nil || o.after > 5*time.Second {
		t.Fatalf("a worker that reported ready was not taken at once: %+v %v after %v", o.ready, o.err, o.after)
	}

	c, _, _ = privateFixture(t)
	c.limits.Ready = time.Hour
	c.fail(errors.New("the worker's output ended"))
	if o := awaited(c); o.ready != nil || o.err == nil || o.err.Error() != "the worker's output ended" {
		t.Fatalf("a fault of the lane did not end the wait with its own error: %+v %v", o.ready, o.err)
	}
}

// A QUERY THE HOST DID NOT ANSWER IN ITS TIME SAYS SO, and only that one.
// The worker reads the reason and says "late" where it used to say the
// enrollment was unavailable (snapshot_bridge.h StorageLate). A query the
// host refused keeps the host's own reason, and one it answered with an
// error it does not classify carries none: neither is a slow disk.
func TestAQueryTheHostDidNotAnswerInTimeSaysSo(t *testing.T) {
	for _, tc := range []struct {
		name string
		q    snapshotQuery
		get  func(context.Context) (aiiosdk.Object, error)
		want string
	}{
		{"a read left unanswered", snapshotQuery{settingsQuery: settingsQuery{1, "s"}},
			func(ctx context.Context) (aiiosdk.Object, error) { <-ctx.Done(); return nil, ctx.Err() }, storageNoAnswer},
		{"a publish left unanswered", snapshotQuery{settingsQuery: settingsQuery{2, "s"}, Action: "publish", Upload: strings.Repeat("a", 64), SHA: strings.Repeat("b", 64), Absent: true},
			func(ctx context.Context) (aiiosdk.Object, error) { <-ctx.Done(); return nil, ctx.Err() }, storageNoAnswer},
		{"the host's own refusal", snapshotQuery{settingsQuery: settingsQuery{3, "s"}},
			func(context.Context) (aiiosdk.Object, error) {
				return aiiosdk.Object(`{"status":"failed","reason_code":"FS_NOT_FOUND"}`), nil
			}, "FS_NOT_FOUND"},
		{"an error with no reason", snapshotQuery{settingsQuery: settingsQuery{4, "s"}},
			func(context.Context) (aiiosdk.Object, error) { return nil, errors.New("refused by the host") }, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c, requests, _ := privateFixture(t)
			c.limits = limits{HostRead: 250 * time.Millisecond, HostWrite: 300 * time.Millisecond, StorageWait: 400 * time.Millisecond, DrainIdle: time.Second, Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: time.Minute, OpeningNotice: 1500 * time.Millisecond}
			go c.readSnapshot(tc.q, tc.get)
			scan := bufio.NewScanner(requests)
			if !scan.Scan() {
				t.Fatal("no outcome reached the worker")
			}
			var out privateRequest
			if err := json.Unmarshal(scan.Bytes(), &out); err != nil || out.Snapshot == nil || out.Snapshot.Error == "" {
				t.Fatalf("not a failed snapshot outcome: %s", scan.Bytes())
			}
			if out.Snapshot.Reason != tc.want {
				t.Fatalf("reason %q, wanted %q: %s", out.Snapshot.Reason, tc.want, scan.Bytes())
			}
		})
	}
}
