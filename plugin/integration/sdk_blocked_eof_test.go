//go:build !wasm_unknown

package aiiosdk

import (
	"bytes"
	"io"
	"testing"
	"time"
)

func TestExternalReviewBlockedResponseAtEOFCannotReturnSuccess(t *testing.T) {
	var frame bytes.Buffer
	if err := WriteFrame(&frame, []byte(`{"jsonrpc":"2.0","id":1,"method":"invoke.call","params":{"operation":"speech.session.close","arguments":{}}}`), MaxControlFrameBytes); err != nil {
		t.Fatal(err)
	}
	idle := &idleReader{entered: make(chan struct{}), release: make(chan struct{})}
	w := &reviewBlockedWriter{entered: make(chan struct{}), release: make(chan struct{})}
	done := make(chan error, 1)
	go func() {
		done <- New("test.blocked-eof").serveSession(io.MultiReader(&frame, idle), w, func(*Session, string, Object) (any, error) { return true, nil })
	}()
	select {
	case <-w.entered:
	case <-time.After(time.Second):
		close(idle.release)
		close(w.release)
		t.Fatal("response write never started")
	}
	close(idle.release) // real EOF while its accepted response write is blocked
	select {
	case err := <-done:
		close(w.release)
		if err == nil {
			t.Fatal("ServeSession returned success while its response write and writer were still unresolved")
		}
	case <-time.After(5 * time.Second):
		close(w.release)
		<-done
		t.Fatal("shutdown failed to honor its bound")
	}
}
