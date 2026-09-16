// Package audio is the host-owned audio endpoint plane (S11 voice, V-E;
// R96): sessions of PCM audio in and out, carried between endpoints the
// host owns — the dashboard's capture and playback, a file, a call — and
// the native T3 speech engine that processes them, under the sample
// clock the endpoint keeps.
//
// Three rules, from the ruling and the engine builder's contract:
//
//   - THE HOST OWNS THE ENDPOINTS. A session binds one input endpoint and
//     one output endpoint, exclusively, for as long as it is open; the
//     engine is handed opaque handles, never devices, and a shadow
//     activation can never seize an endpoint a live session holds.
//   - THE SAMPLE CLOCK IS THE ENDPOINT'S. Every frame carries its stream,
//     its sequence and its sample span (start inclusive, end exclusive);
//     a gap is declared as a discontinuity, never dropped silently; a
//     stream ends with an explicit end sample. Batching never redefines
//     when the speaker finished.
//   - NO AUDIO LEAVES THE HOST WHILE SAFE HOLDS. Under SAFE the plane
//     binds only a contained engine, and never a remote endpoint.
//
// The plane carries one format on the wire, PCM s16le at the session's
// declared rate and channels; resampling and echo cancellation belong to
// the endpoint that produces or consumes the audio, declared, never done
// here.
package audio

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync"
)

// Format is a stream's PCM shape: s16le at Rate samples per second with
// Channels interleaved channels.
type Format struct {
	Rate     int
	Channels int
}

// BytesPerSample is the width of one interleaved sample group.
func (f Format) BytesPerSample() int { return 2 * f.Channels }

func (f Format) String() string { return fmt.Sprintf("s16le/%d/%d", f.Rate, f.Channels) }

// Kind is what a frame carries.
type Kind uint8

const (
	// KindPCM carries samples: Start is the first sample's index and
	// PCM holds Samples() interleaved sample groups.
	KindPCM Kind = 1
	// KindDiscontinuity declares a gap: samples before Start were lost
	// or never captured. It carries no audio. The next PCM frame starts
	// at Start or later.
	KindDiscontinuity Kind = 2
	// KindEnd ends the stream: Start is its EXCLUSIVE end sample. No
	// frame of the stream follows it.
	KindEnd Kind = 3
)

// Frame is one unit on the plane: a stream, a sequence, a sample span.
type Frame struct {
	Kind   Kind
	Stream uint32
	Seq    uint32
	Start  int64  // the first sample of this frame (inclusive); for KindEnd the exclusive end
	PCM    []byte // s16le interleaved; empty except for KindPCM
}

// Samples is the number of sample groups this frame carries.
func (fr Frame) Samples(f Format) int64 { return int64(len(fr.PCM) / f.BytesPerSample()) }

// End is the exclusive end sample of this frame's span.
func (fr Frame) End(f Format) int64 {
	if fr.Kind == KindPCM {
		return fr.Start + fr.Samples(f)
	}
	return fr.Start
}

// Source produces a stream of frames in order under its own sample
// clock. Read returns io.EOF after the stream's KindEnd.
type Source interface {
	Format() Format
	Read(ctx context.Context) (Frame, error)
}

// Sink consumes a stream of frames in order.
type Sink interface {
	Format() Format
	Write(ctx context.Context, fr Frame) error
	Close() error
}

// Endpoint is one host-owned source and/or sink with a label the
// identity reads (browser, file, twilio). Remote endpoints carry audio
// off the host and are refused under SAFE.
type Endpoint struct {
	ID     string
	Label  string
	Remote bool
	Source Source // nil for an output-only endpoint
	Sink   Sink   // nil for an input-only endpoint
}

var (
	ErrEndpointUnknown = errors.New("audio: no such endpoint")
	ErrEndpointBusy    = errors.New("audio: the endpoint is bound to another session")
	ErrNotASource      = errors.New("audio: the endpoint has no input")
	ErrNotASink        = errors.New("audio: the endpoint has no output")
	// ErrSafe is a binding SAFE refuses: an uncontained engine, or a
	// remote endpoint — either would let audio leave the host.
	ErrSafe = errors.New("audio: no audio leaves the host while SAFE holds")
)

// Plane is the registry of endpoints and their bindings.
type Plane struct {
	mu        sync.Mutex
	endpoints map[string]*Endpoint
	bound     map[string]string // endpoint id -> session id
	// SafeMode reports the host's SAFE posture; nil means never SAFE.
	SafeMode func() (reason string, safe bool)
}

// NewPlane makes an empty plane.
func NewPlane() *Plane {
	return &Plane{endpoints: map[string]*Endpoint{}, bound: map[string]string{}}
}

// Register adds an endpoint; an id already registered is refused.
func (p *Plane) Register(ep *Endpoint) error {
	if ep == nil || ep.ID == "" {
		return errors.New("audio: an endpoint needs an id")
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if _, dup := p.endpoints[ep.ID]; dup {
		return fmt.Errorf("audio: endpoint %q is already registered", ep.ID)
	}
	p.endpoints[ep.ID] = ep
	return nil
}

// Unregister removes an endpoint that is not bound.
func (p *Plane) Unregister(id string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if s, busy := p.bound[id]; busy {
		return fmt.Errorf("%w (session %s)", ErrEndpointBusy, s)
	}
	delete(p.endpoints, id)
	return nil
}

// Endpoints lists the registered endpoints and the session each is
// bound to, if any.
func (p *Plane) Endpoints() []EndpointState {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make([]EndpointState, 0, len(p.endpoints))
	for id, ep := range p.endpoints {
		out = append(out, EndpointState{ID: id, Label: ep.Label, Remote: ep.Remote, Input: ep.Source != nil, Output: ep.Sink != nil, BoundTo: p.bound[id]})
	}
	return out
}

// EndpointState is one endpoint as the plane reports it.
type EndpointState struct {
	ID, Label     string
	Remote        bool
	Input, Output bool
	BoundTo       string
}

// Binding is one session's exclusive hold on an input and an output
// endpoint, with the opaque handles the engine is given.
type Binding struct {
	SessionID    string
	InputID      string
	OutputID     string
	InputHandle  string
	OutputHandle string
	// InFormat and OutFormat are the endpoints' own formats: the clock
	// the source keeps and the clock the sink expects. They need not
	// agree with each other or with the engine — the pump converts at
	// the host's endpoints to the formats the engine answers with.
	InFormat  Format
	OutFormat Format
	Source    Source
	Sink      Sink
	// Contained is the engine's containment as the binder declared it;
	// Remote is true when either endpoint leaves the host. SAFE decides
	// on exactly these two, at entry and when it begins mid-session.
	Contained bool
	Remote    bool

	plane    *Plane
	once     sync.Once
	released chan struct{}
}

// Bind holds the input and the output endpoint for sessionID and hands
// back their handles. Exclusive: an endpoint another session holds is
// refused. Under SAFE the binding is refused unless the engine is
// contained and neither endpoint is remote.
func (p *Plane) Bind(sessionID, inputID, outputID string, contained bool) (*Binding, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	in, ok := p.endpoints[inputID]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrEndpointUnknown, inputID)
	}
	out, ok := p.endpoints[outputID]
	if !ok {
		return nil, fmt.Errorf("%w: %s", ErrEndpointUnknown, outputID)
	}
	if in.Source == nil {
		return nil, fmt.Errorf("%w: %s", ErrNotASource, inputID)
	}
	if out.Sink == nil {
		return nil, fmt.Errorf("%w: %s", ErrNotASink, outputID)
	}
	if p.SafeMode != nil {
		if reason, safe := p.SafeMode(); safe {
			if !contained {
				return nil, fmt.Errorf("%w: the engine is not contained (%s)", ErrSafe, reason)
			}
			if in.Remote || out.Remote {
				return nil, fmt.Errorf("%w: a remote endpoint (%s)", ErrSafe, reason)
			}
		}
	}
	for _, id := range []string{inputID, outputID} {
		if s, busy := p.bound[id]; busy && s != sessionID {
			return nil, fmt.Errorf("%w: %s is held by session %s", ErrEndpointBusy, id, s)
		}
	}
	p.bound[inputID] = sessionID
	p.bound[outputID] = sessionID
	b := &Binding{
		SessionID: sessionID, InputID: inputID, OutputID: outputID,
		InputHandle: "in:" + sessionID + ":" + inputID, OutputHandle: "out:" + sessionID + ":" + outputID,
		InFormat: in.Source.Format(), OutFormat: out.Sink.Format(), Source: in.Source, Sink: out.Sink,
		Contained: contained, Remote: in.Remote || out.Remote,
		plane: p, released: make(chan struct{}),
	}
	return b, nil
}

// Release gives both endpoints back. It is the host's act at the
// session's terminal closure or the child's verified reap — never at a
// close merely admitted — and it happens once.
func (b *Binding) Release() {
	b.once.Do(func() {
		b.plane.mu.Lock()
		if b.plane.bound[b.InputID] == b.SessionID {
			delete(b.plane.bound, b.InputID)
		}
		if b.plane.bound[b.OutputID] == b.SessionID {
			delete(b.plane.bound, b.OutputID)
		}
		b.plane.mu.Unlock()
		close(b.released)
	})
}

// Released fires once the endpoints have been given back.
func (b *Binding) Released() <-chan struct{} { return b.released }

// Channel is the engine's side of the audio path: input frames go to
// the engine, output frames come from it. The supervisor provides one
// over the child's inherited descriptors.
type Channel interface {
	WriteInput(fr Frame) error
	ReadOutput() (Frame, error)
	Close() error
}

// Pump moves audio between the binding's endpoints and the engine's
// channel until the input ends, the context ends, or the channel
// fails: the input pump reads the source and writes the engine; the
// output pump reads the engine and writes the sink. Cutoff, when set,
// is the exclusive end sample after which no input is delivered: the
// finish_input boundary, honored exactly.
type Pump struct {
	b  *Binding
	ch Channel
	// Stream is the input stream id the pump stamps on every frame it
	// delivers; zero keeps the source's own. Sessions never reuse one.
	Stream uint32
	// EngineIn and EngineOut are the formats the engine speaks, as it
	// answered the open. THE CONVERSION IS THE HOST'S, AT ITS ENDPOINTS:
	// the source's format to EngineIn on the way in, EngineOut to the
	// sink's format on the way out, each along its sample clock. A zero
	// value means the endpoint's own format. Set before Run.
	EngineIn  Format
	EngineOut Format

	mu        sync.Mutex
	inSeq     uint32
	delivered int64 // input samples delivered to the engine (exclusive end)
	received  int64 // output samples received from the engine
	cutoff    int64 // -1: none
	inDone    bool
	inErr     error
	outErr    error
	dropped   uint64 // output frames the sink refused
	done      chan struct{}
}

// NewPump prepares the pumps for a binding over a channel.
func NewPump(b *Binding, ch Channel) *Pump {
	return &Pump{b: b, ch: ch, cutoff: -1, done: make(chan struct{})}
}

// Run drives both pumps and returns when both have ended. The input
// pump ends at the source's end, at the cutoff, or on error; the output
// pump ends when the session/context ends or the engine channel ends or
// fails. A per-synthesis END flushes only that output stream. The channel is closed on return.
func (p *Pump) Run(ctx context.Context) {
	defer close(p.done)
	defer p.ch.Close()
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); p.runInput(ctx) }()
	go func() { defer wg.Done(); p.runOutput(ctx) }()
	wg.Wait()
}

// Done fires when both pumps have ended.
func (p *Pump) Done() <-chan struct{} { return p.done }

func (p *Pump) engineIn() Format {
	if p.EngineIn.Rate == 0 {
		return p.b.InFormat
	}
	return p.EngineIn
}

func (p *Pump) engineOut() Format {
	if p.EngineOut.Rate == 0 {
		return p.b.OutFormat
	}
	return p.EngineOut
}

// EngineCutoff maps a boundary in the source's clock to the engine's:
// the position the engine is told in finish_input, and the position the
// input stream's end carries. Exact at an integer ratio; otherwise the
// next whole sample of the coarser clock.
func (p *Pump) EngineCutoff(endSample int64) int64 {
	return NewResampler(p.b.InFormat, p.engineIn()).TargetPos(endSample)
}

// writeInput stamps the next input sequence and the stream and delivers
// one frame to the engine.
func (p *Pump) writeInput(fr Frame) error {
	p.mu.Lock()
	p.inSeq++
	fr.Seq = p.inSeq
	if p.Stream != 0 {
		fr.Stream = p.Stream
	}
	p.mu.Unlock()
	if err := p.ch.WriteInput(fr); err != nil {
		p.mu.Lock()
		p.inErr, p.inDone = err, true
		p.mu.Unlock()
		return err
	}
	return nil
}

func (p *Pump) runInput(ctx context.Context) {
	f, eng := p.b.InFormat, p.engineIn()
	rs := NewResampler(f, eng)
	convert := f != eng
	for {
		fr, err := p.b.Source.Read(ctx)
		if err != nil {
			p.mu.Lock()
			if err != io.EOF {
				p.inErr = err
			}
			p.inDone = true
			p.mu.Unlock()
			return
		}
		p.mu.Lock()
		cutoff := p.cutoff
		p.mu.Unlock()
		if cutoff >= 0 && fr.Kind == KindPCM {
			// The finish_input boundary: deliver through the cutoff,
			// exclusive, and end the stream there — never a sample past it,
			// never a sample before it lost.
			if fr.Start >= cutoff {
				p.endInput(cutoff, rs, convert)
				return
			}
			if fr.End(f) > cutoff {
				fr.PCM = fr.PCM[:int(cutoff-fr.Start)*f.BytesPerSample()]
			}
		}
		if fr.Kind == KindEnd {
			p.endInput(fr.Start, rs, convert)
			return
		}
		// Delivered is kept in the SOURCE's clock: the page's, the
		// operator's — the clock a cutoff is named in.
		p.mu.Lock()
		if fr.Kind == KindPCM {
			p.delivered = fr.End(f)
		} else {
			p.delivered = fr.Start
		}
		p.mu.Unlock()
		if convert {
			switch fr.Kind {
			case KindPCM:
				start := rs.Position()
				rs.Feed(fr.PCM)
				pcm := rs.Take()
				if len(pcm) == 0 {
					continue // the kernel has not reached a whole engine sample yet
				}
				fr.PCM, fr.Start = pcm, start
			case KindDiscontinuity:
				// The segment ends here: its tail first, then the
				// declaration at the position the engine's clock gives it.
				if tail := rs.Finish(); len(tail) > 0 {
					if err := p.writeInput(Frame{Kind: KindPCM, Start: rs.Position() - int64(len(tail)/eng.BytesPerSample()), PCM: tail}); err != nil {
						return
					}
				}
				rs.Reset(fr.Start)
				fr.Start = rs.Position()
			}
		}
		if err := p.writeInput(fr); err != nil {
			return
		}
		if cutoff >= 0 && fr.Kind == KindPCM && p.Delivered() == cutoff {
			p.endInput(cutoff, rs, convert)
			return
		}
	}
}

// endInput sends the stream's end at the exclusive sample end — the
// converted tail first, when the engine's clock differs.
func (p *Pump) endInput(end int64, rs *Resampler, convert bool) {
	p.mu.Lock()
	p.delivered = end
	p.inDone = true
	stream := p.Stream
	p.mu.Unlock()
	if stream == 0 {
		stream = 1
	}
	engineEnd := end
	if convert {
		eng := p.engineIn()
		if tail := rs.Finish(); len(tail) > 0 {
			if err := p.writeInput(Frame{Kind: KindPCM, Stream: stream, Start: rs.Position() - int64(len(tail)/eng.BytesPerSample()), PCM: tail}); err != nil {
				return
			}
		}
		engineEnd = rs.TargetPos(end)
	}
	_ = p.writeInput(Frame{Kind: KindEnd, Stream: stream, Start: engineEnd})
}

func (p *Pump) runOutput(ctx context.Context) {
	defer p.b.Sink.Close()
	eng, out := p.engineOut(), p.b.OutFormat
	convert := eng != out
	// One converter per output stream: each reply is its own clock.
	rss := map[uint32]*Resampler{}
	seqs := map[uint32]uint32{}
	write := func(fr Frame) {
		if convert {
			seqs[fr.Stream]++
			fr.Seq = seqs[fr.Stream] // the sink's sequence counts the sink's frames
		}
		if err := p.b.Sink.Write(ctx, fr); err != nil {
			p.mu.Lock()
			p.dropped++
			p.mu.Unlock()
		}
	}
	for {
		fr, err := p.ch.ReadOutput()
		if err != nil {
			p.mu.Lock()
			if err != io.EOF {
				p.outErr = err
			}
			p.mu.Unlock()
			return
		}
		if fr.Kind == KindPCM {
			// Received is kept in the ENGINE's clock: what the engine said.
			p.mu.Lock()
			p.received = fr.End(eng)
			p.mu.Unlock()
		}
		if convert {
			rs := rss[fr.Stream]
			if rs == nil {
				rs = NewResampler(eng, out)
				rs.Reset(fr.Start)
				rss[fr.Stream] = rs
			}
			switch fr.Kind {
			case KindPCM:
				start := rs.Position()
				rs.Feed(fr.PCM)
				pcm := rs.Take()
				if len(pcm) == 0 {
					continue
				}
				fr.PCM, fr.Start = pcm, start
			case KindDiscontinuity, KindEnd:
				if tail := rs.Finish(); len(tail) > 0 {
					write(Frame{Kind: KindPCM, Stream: fr.Stream, Start: rs.Position() - int64(len(tail)/out.BytesPerSample()), PCM: tail})
				}
				pos := rs.TargetPos(fr.Start)
				if fr.Kind == KindEnd {
					delete(rss, fr.Stream)
				} else {
					rs.Reset(fr.Start)
				}
				fr.Start = pos
			}
		}
		write(fr)
		// END retires one synthesis stream, not the resident output lane.
		// Only the channel/session lifetime ends this pump.
		if fr.Kind == KindEnd {
			delete(seqs, fr.Stream)
		}
		if ctx.Err() != nil {
			return
		}
	}
}

// Cutoff fixes the exclusive end sample of the input: the finish_input
// boundary. It returns the boundary the pump will honor — the sample
// given, or the samples already delivered when the given one is
// already behind — so the host tells the engine the truth.
func (p *Pump) Cutoff(endSample int64) int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.inDone {
		return p.delivered
	}
	if endSample < p.delivered {
		endSample = p.delivered
	}
	p.cutoff = endSample
	return endSample
}

// Delivered is the exclusive end of the input delivered so far — the
// boundary a finish_input names when the operator stops speaking now.
func (p *Pump) Delivered() int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.delivered
}

// Received is the exclusive end of the output received so far.
func (p *Pump) Received() int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.received
}

// Errors reports the count of output frames the sink refused and the
// pumps' failures, if any.
func (p *Pump) Errors() (dropped uint64, in, out error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.dropped, p.inErr, p.outErr
}
