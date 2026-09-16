//go:build !wasm_unknown

package aiiosdk

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

func pendingLane(t *testing.T, admit SessionAdmit) (*os.File, *os.File, <-chan error) {
	t.Helper()
	in, send, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	read, out, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- New("test.pending").serveSession(in, out, admit) }()
	t.Cleanup(func() { send.Close(); read.Close(); in.Close(); out.Close() })
	return send, read, done
}
func pendingSend(t *testing.T, send *os.File, id int) {
	t.Helper()
	send.SetWriteDeadline(time.Now().Add(time.Second))
	frame := []byte(fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"invoke.call","params":{"operation":"%d","arguments":{}}}`, id, id))
	if err := WriteFrame(send, frame, MaxControlFrameBytes); err != nil {
		t.Fatal(err)
	}
}
func pendingRead(t *testing.T, read *os.File) map[string]json.RawMessage {
	t.Helper()
	read.SetReadDeadline(time.Now().Add(time.Second))
	raw, err := ReadFrame(read, MaxControlFrameBytes)
	if err != nil {
		t.Fatal(err)
	}
	var result map[string]json.RawMessage
	if err := json.Unmarshal(raw, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestPendingAdmissionDoesNotBlockOrderedStopAndPreservesRefusal(t *testing.T) {
	first := make(chan AdmissionResult, 1)
	order := make(chan string, 3)
	send, read, done := pendingLane(t, func(s *Session, op string, _ Object) (any, error) {
		order <- op
		if op == "1" {
			return PendingAdmission(first), nil
		}
		return map[string]bool{"fenced": true}, nil
	})
	pendingSend(t, send, 1)
	pendingSend(t, send, 2)
	pendingSend(t, send, 3)
	for _, want := range []string{"2", "3"} {
		r := pendingRead(t, read)
		if string(r["id"]) != want || !hasField(r["result"], "fenced") {
			t.Fatalf("delayed result blocked or miscorrelated controls: %s", r)
		}
	}
	for _, want := range []string{"1", "2", "3"} {
		if got := <-order; got != want {
			t.Fatalf("enqueue reordered: %s want %s", got, want)
		}
	}
	first <- AdmissionResult{Err: errors.New("engine refused exact cause")}
	r := pendingRead(t, read)
	if string(r["id"]) != "1" || !strings.Contains(string(r["error"]), "engine refused exact cause") {
		t.Fatalf("fabricated acceptance or lost refusal: %s", r)
	}
	send.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("lane did not retire")
	}
}

func TestPendingAdmissionEOFReleasesAllUnresolvedWaiters(t *testing.T) {
	unresolved := make(chan AdmissionResult)
	admitted := make(chan struct{}, 1)
	send, _, done := pendingLane(t, func(*Session, string, Object) (any, error) {
		admitted <- struct{}{}
		return PendingAdmission(unresolved), nil
	})
	pendingSend(t, send, 1)
	<-admitted
	send.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("unresolved result leaked on EOF")
	}
}

func TestPendingAdmissionClosedWithoutVerdictFaultsLane(t *testing.T) {
	unresolved := make(chan AdmissionResult)
	close(unresolved)
	send, _, done := pendingLane(t, func(*Session, string, Object) (any, error) { return PendingAdmission(unresolved), nil })
	pendingSend(t, send, 1)
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "without a verdict") {
			t.Fatalf("missing admission certified: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("fault did not release reader")
	}
}

func TestPendingAdmissionCapacityIsBounded(t *testing.T) {
	never := make(chan AdmissionResult)
	admitted := make(chan struct{}, sessionAdmitQueue+1)
	send, _, done := pendingLane(t, func(*Session, string, Object) (any, error) {
		admitted <- struct{}{}
		return PendingAdmission(never), nil
	})
	for i := 1; i <= sessionAdmitQueue; i++ {
		pendingSend(t, send, i)
		<-admitted
	}
	pendingSend(t, send, sessionAdmitQueue+1)
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "unresolved") {
			t.Fatalf("unbounded admission: %v", err)
		}
	case <-time.After(time.Second):
		stack := make([]byte, 256<<10)
		n := runtime.Stack(stack, true)
		t.Fatalf("overflow did not fault\n%s", stack[:n])
	}
	if len(admitted) != 0 {
		t.Fatal("overflow reached engine")
	}
}

func TestBlockedResponseWriterDoesNotBlockLaterControlAdmission(t *testing.T) {
	in, send, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	w := &reviewBlockedWriter{entered: make(chan struct{}), release: make(chan struct{})}
	admitted := make(chan string, 2)
	done := make(chan error, 1)
	go func() {
		done <- New("test.pending").serveSession(in, w, func(_ *Session, op string, _ Object) (any, error) { admitted <- op; return true, nil })
	}()
	t.Cleanup(func() { send.Close(); in.Close() })
	pendingSend(t, send, 1)
	<-w.entered
	<-admitted
	pendingSend(t, send, 2)
	select {
	case op := <-admitted:
		if op != "2" {
			t.Fatal(op)
		}
	case <-time.After(time.Second):
		close(w.release)
		t.Fatal("response write blocked stop admission")
	}
	close(w.release)
	send.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("writer did not retire")
	}
}

