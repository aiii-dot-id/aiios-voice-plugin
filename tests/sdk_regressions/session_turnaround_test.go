//go:build !wasm_unknown

package aiiosdk

import (
	"bytes"
	"encoding/json"
	"testing"
	"time"
)

type replyTurnaroundWriter struct{ visible func() }

func (w replyTurnaroundWriter) Write(p []byte) (int, error) {
	if bytes.Contains(p, []byte(`"id":1,`)) {
		w.visible()
	}
	return len(p), nil
}

func TestVisibleReplyAlreadyFreedItsAdmissionReservation(t *testing.T) {
	s := &Session{closed: make(chan struct{}), admissionSlots: make(chan struct{}, sessionAdmitQueue)}
	for i := 0; i < sessionAdmitQueue; i++ {
		s.admissionSlots <- struct{}{}
	}
	admitted := make(chan error, 1)
	s.out = replyTurnaroundWriter{visible: func() {
		// The peer has consumed reply 1 and sent control 2, but the
		// writer has not returned from Write yet. No sleeps or races.
		admitted <- s.admitOne(func(c *Control) { c.Answer(true, nil) }, []byte(`{"id":2,"params":{"operation":"next","arguments":{}}}`))
	}}
	c := &Control{reply: &controlReply{s: s, id: json.RawMessage("1")}}
	c.Answer(true, nil)
	t.Cleanup(func() {
		s.end()
		select {
		case <-s.writerDone:
		case <-time.After(time.Second):
			t.Error("writer did not retire")
		}
	})
	select {
	case err := <-admitted:
		if err != nil {
			t.Fatalf("peer read the reply, but its next control was refused: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("reply was not written")
	}
}
