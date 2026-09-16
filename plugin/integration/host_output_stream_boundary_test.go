// Inject into internal/audio with -overlay; no models, devices or host edits.
package audio

import (
	"context"
	"fmt"
	"io"
	"testing"
)

type residentBoundaryChannel struct{ frames []Frame }

func (*residentBoundaryChannel) WriteInput(Frame) error { return nil }
func (*residentBoundaryChannel) Close() error           { return nil }
func (c *residentBoundaryChannel) ReadOutput() (Frame, error) {
	if len(c.frames) == 0 {
		return Frame{}, io.EOF
	}
	fr := c.frames[0]
	c.frames = c.frames[1:]
	return fr, nil
}

func TestResidentOutputENDDoesNotEndNextReply(t *testing.T) {
	for _, rate := range []int{24000, 48000} {
		t.Run(fmt.Sprintf("speaker_%d", rate), func(t *testing.T) {
			ch := &residentBoundaryChannel{}
			for _, stream := range []uint32{1, 2} {
				ch.frames = append(ch.frames,
					Frame{Kind: KindPCM, Stream: stream, Seq: 1, PCM: make([]byte, 642)},
					Frame{Kind: KindEnd, Stream: stream, Seq: 2, Start: 321})
			}
			f := Format{Rate: rate, Channels: 1}
			sink := NewCaptureSink(f)
			pump := NewPump(&Binding{OutFormat: f, Sink: sink}, ch)
			pump.EngineOut = Format{Rate: 24000, Channels: 1}
			pump.runOutput(context.Background())
			want := int64(321 * rate / 24000)
			for _, stream := range []uint32{1, 2} {
				if got := int64(len(sink.StreamPCM(stream)) / 2); got != want || sink.StreamEnd(stream) != want {
					t.Errorf("reply stream %d: samples=%d END=%d want=%d; unread engine frames=%d", stream, got, sink.StreamEnd(stream), want, len(ch.frames))
				}
			}
			if len(ch.frames) != 0 {
				t.Errorf("per-reply END stopped the resident pump with %d frames unread", len(ch.frames))
			}
		})
	}
}
