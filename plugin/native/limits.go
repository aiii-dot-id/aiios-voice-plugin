package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

type limits struct {
	HostRead time.Duration

	HostWrite time.Duration

	StorageWait time.Duration

	DrainIdle time.Duration

	ReplySettings time.Duration

	Abort        time.Duration
	CaptureClose time.Duration
	SessionOpen  time.Duration

	OpeningNotice time.Duration

	Control    time.Duration
	AudioWrite time.Duration

	Ready time.Duration

	ControlWrite time.Duration

	Retire      time.Duration
	CaptureTail time.Duration

	WorkerExit time.Duration
	WorkerReap time.Duration
	LaneFlush  time.Duration

	ModelCall time.Duration
	InputTail time.Duration

	OutputTake time.Duration

	SpeakerMatch time.Duration

	WarmProbe time.Duration

	EndpointDecision time.Duration
	EndpointRetire   time.Duration

	SeparationMin time.Duration
	SeparationMax time.Duration
}

var defaultLimits = limits{HostRead: 5 * time.Second, HostWrite: 12 * time.Second, StorageWait: 12 * time.Second, DrainIdle: 15 * time.Second, ReplySettings: 150 * time.Millisecond,
	Abort: 5 * time.Second, CaptureClose: 45 * time.Second, SessionOpen: 60 * time.Second, OpeningNotice: 1500 * time.Millisecond,
	Control: 2 * time.Second, AudioWrite: 3 * time.Second, Ready: 175 * time.Second, ControlWrite: 3 * time.Second,
	Retire: 5 * time.Second, CaptureTail: 2 * time.Second, WorkerExit: 5500 * time.Millisecond, WorkerReap: 5 * time.Second, LaneFlush: 2 * time.Second,
	ModelCall: 30 * time.Second, InputTail: 3 * time.Second, OutputTake: 15 * time.Second, SpeakerMatch: 15 * time.Second, WarmProbe: 40 * time.Second,
	EndpointDecision: 1000 * time.Millisecond, EndpointRetire: 30500 * time.Millisecond, SeparationMin: 4 * time.Second, SeparationMax: 25 * time.Second}

const hostInvokeAllowance = 90 * time.Second

const limitMargin = 500 * time.Millisecond

const hostAnswerMargin = 2 * time.Second

const (
	limitFloor   = 250 * time.Millisecond
	limitCeiling = 120 * time.Second

	replyFloor   = 10 * time.Millisecond
	replyCeiling = 2 * time.Second

	readyFloor   = 1000 * time.Millisecond
	readyCeiling = 3600 * time.Second
)

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

	if l.playbackReport()+hostAnswerMargin > hostInvokeAllowance {
		return fmt.Errorf("runtime limits: a playback report would be given %d ms, and the host waits %d ms for one invocation", l.playbackReport().Milliseconds(), hostInvokeAllowance.Milliseconds())
	}

	if l.WorkerExit < l.Retire+limitMargin {
		return fmt.Errorf("runtime limits: this carrier would wait %d ms for its worker to exit and the worker has %d ms to retire; the wait must be the longer by %d ms at least", l.WorkerExit.Milliseconds(), l.Retire.Milliseconds(), limitMargin.Milliseconds())
	}

	if l.OutputTake < l.AudioWrite+limitMargin {
		return fmt.Errorf("runtime limits: the engine would wait %d ms for its audio to be taken (output_take_ms) and one write of that audio to the host is given %d ms (audio_write_ms); the wait must be the longer by %d ms at least", l.OutputTake.Milliseconds(), l.AudioWrite.Milliseconds(), limitMargin.Milliseconds())
	}

	if l.DrainIdle < l.InputTail+limitMargin {
		return fmt.Errorf("runtime limits: a drain may go %d ms with nothing moving (drain_idle_ms) and a conversation's last frames are waited for %d ms (input_tail_ms); the drain's must be the longer by %d ms at least", l.DrainIdle.Milliseconds(), l.InputTail.Milliseconds(), limitMargin.Milliseconds())
	}

	if l.Ready < l.WarmProbe+limitMargin {
		return fmt.Errorf("runtime limits: a warm inference is given %d ms (warm_probe_ms) and the worker's readiness %d ms (ready_ms); readiness must be the longer by %d ms at least", l.WarmProbe.Milliseconds(), l.Ready.Milliseconds(), limitMargin.Milliseconds())
	}

	if l.EndpointRetire < l.ModelCall+limitMargin {
		return fmt.Errorf("runtime limits: an endpoint question is waited for %d ms at the input's end (endpoint_retire_ms) and the model call it is has %d ms (model_call_ms); the wait must be the longer by %d ms at least", l.EndpointRetire.Milliseconds(), l.ModelCall.Milliseconds(), limitMargin.Milliseconds())
	}

	if l.SeparationMax < l.SeparationMin {
		return fmt.Errorf("runtime limits: a separation's budget is at least %d ms (separation_min_ms) and at most %d ms (separation_max_ms); the most must not be the less", l.SeparationMin.Milliseconds(), l.SeparationMax.Milliseconds())
	}
	if l.ModelCall < l.SeparationMax+limitMargin {
		return fmt.Errorf("runtime limits: a separation may take %d ms (separation_max_ms) and the model call it runs in is given %d ms (model_call_ms); the call's must be the longer by %d ms at least", l.SeparationMax.Milliseconds(), l.ModelCall.Milliseconds(), limitMargin.Milliseconds())
	}
	return nil
}

func (l limits) query(action string) time.Duration {
	if action == "" {
		return l.HostRead
	}
	return l.HostWrite
}

func (l limits) workerExchange(action string) time.Duration { return l.query(action) + limitMargin }

func (l limits) opening() time.Duration { return l.StorageWait }

func (l limits) workerOpening() time.Duration { return l.opening() + limitMargin }

func (l limits) wholeRead() time.Duration { return l.StorageWait + limitMargin }

func (l limits) wholePublication() time.Duration {
	return 2*l.workerExchange("stage") + l.workerExchange("publish") + l.wholeRead()
}

func (l limits) storageOperation() time.Duration {
	return 2*l.wholeRead() + l.wholePublication() + l.Control
}

func (l limits) playbackReport() time.Duration { return l.AudioWrite + l.Control }

func (l limits) ownStorage() time.Duration { return 2*l.HostRead + 2*l.HostWrite }

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

const limitsEnv = "AII_VOICE_LIMITS"

func (l limits) environment() string {
	b, _ := json.Marshal(l.forWorker())
	return limitsEnv + "=" + string(b)
}

func profileLimitNames() map[string]json.RawMessage {
	v := int64(0)
	all, _ := json.Marshal(profileLimits{&v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v, &v})
	var names map[string]json.RawMessage
	_ = json.Unmarshal(all, &names)
	return names
}

func (p *profileLimits) UnmarshalJSON(data []byte) error {
	var stated map[string]json.RawMessage
	if err := json.Unmarshal(data, &stated); err != nil || stated == nil {
		return errors.New("runtime limits must be an object of whole milliseconds by name")
	}
	known := profileLimitNames()

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

	type members profileLimits
	var m members
	if err := json.Unmarshal(data, &m); err != nil {
		return fmt.Errorf("runtime limits must be whole numbers of milliseconds: %w", err)
	}
	*p = profileLimits(m)
	return nil
}
