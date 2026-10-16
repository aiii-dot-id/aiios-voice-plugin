//go:build !wasm_unknown

package aiiosdk

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"sync"
	"testing"
	"time"
)

type replyReserveWriter struct {
	bytes.Buffer
	entered, release chan struct{}
	once             sync.Once
}

func (w *replyReserveWriter) Write(p []byte) (int, error) {
	w.once.Do(func() { close(w.entered); <-w.release })
	return w.Buffer.Write(p)
}

func TestFailedAdmissionWriteReleasesItsReservedSlot(t *testing.T) {
	s := &Session{out: faultWriter{}, closed: make(chan struct{}), admissionSlots: make(chan struct{}, sessionAdmitQueue)}
	s.admissionSlots <- struct{}{}
	c := &Control{reply: &controlReply{s: s, id: json.RawMessage("1")}}
	c.Answer(true, nil)
	select {
	case <-s.writerDone:
	case <-time.After(5 * time.Second):
		t.Fatal("failed writer did not retire")
	}
	if s.faultErr() == nil || len(s.admissionSlots) != 0 {
		t.Fatalf("write failure did not settle custody: slots=%d err=%v", len(s.admissionSlots), s.faultErr())
	}
	c.Answer(false, nil) // already settled: neither publish nor release twice
}

func TestEventBacklogPreservesEveryAdmittedControlReply(t *testing.T) {
	w := &replyReserveWriter{entered: make(chan struct{}), release: make(chan struct{})}
	s := &Session{out: w, closed: make(chan struct{}), admissionSlots: make(chan struct{}, sessionAdmitQueue)}
	var release sync.Once
	t.Cleanup(func() {
		s.end()
		release.Do(func() { close(w.release) })
		select {
		case <-s.writerDone:
		case <-time.After(5 * time.Second):
			t.Error("writer did not retire")
		}
	})
	if err := s.Emit(map[string]any{"sequence": 0}); err != nil {
		t.Fatal(err)
	}
	<-w.entered // the writer holds the first event; no capacity is being freed
	const events = 64
	for i := 1; i <= events; i++ {
		if err := s.Emit(map[string]any{"sequence": i}); err != nil {
			t.Fatal(err)
		}
	}
	for i := 1; i <= sessionAdmitQueue; i++ {
		s.admissionSlots <- struct{}{}
		c := &Control{reply: &controlReply{s: s, id: json.RawMessage(fmt.Sprint(i))}}
		c.Answer(map[string]bool{"accepted": true}, nil)
	}
	if err := s.faultErr(); err != nil {
		t.Fatalf("engine events displaced a control reply: %v", err)
	}
	if got := len(s.admissionSlots); got != sessionAdmitQueue {
		t.Fatalf("undelivered replies relinquished their reserved slots: %d", got)
	}
	s.end()
	release.Do(func() { close(w.release) })
	select {
	case <-s.writerDone:
	case <-time.After(5 * time.Second):
		t.Fatal("delivery never completed")
	}
	if len(s.admissionSlots) != 0 {
		t.Fatal("delivered answers retained their slots")
	}
	seen := make(map[int]bool)
	notices := 0
	for {
		frame, err := ReadFrame(&w.Buffer, MaxControlFrameBytes)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		var m struct {
			ID     int             `json:"id"`
			Method string          `json:"method"`
			Result json.RawMessage `json:"result"`
		}
		if err := json.Unmarshal(frame, &m); err != nil {
			t.Fatal(err)
		}
		if m.Method != "" {
			notices++
			continue
		}
		if m.ID < 1 || m.ID > sessionAdmitQueue || seen[m.ID] || len(m.Result) == 0 {
			t.Fatalf("invalid or duplicate reply: %s", frame)
		}
		seen[m.ID] = true
	}
	if notices != events+1 || len(seen) != sessionAdmitQueue {
		t.Fatalf("lost delivery: events=%d replies=%d", notices, len(seen))
	}
}
