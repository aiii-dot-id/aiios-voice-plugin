package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

type realTenant struct {
	process *sessionProcess
	events  chan map[string]any
	replies chan map[string]any
	ready   json.RawMessage
}

func startRealTenant(t *testing.T, pending *pendingChannel, name string) *realTenant {
	t.Helper()
	audioIn, toAudio, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	fromAudio, audioOut, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	process, err := pending.Start(audioIn, audioOut)
	if err != nil {
		t.Fatalf("%s: start: %v", name, err)
	}
	t.Cleanup(func() { toAudio.Close() })
	go func() { _, _ = io.Copy(io.Discard, fromAudio); fromAudio.Close() }()
	tn := &realTenant{process: process, events: make(chan map[string]any, 64), replies: make(chan map[string]any, 8)}
	lines := bufio.NewReader(process.Stdout)
	first, err := lines.ReadBytes('\n')
	var ready struct {
		Ready json.RawMessage `json:"ready"`
	}
	if err != nil || json.Unmarshal(first, &ready) != nil || len(ready.Ready) == 0 {
		t.Fatalf("%s: no ready line: %q %v", name, first, err)
	}
	tn.ready = ready.Ready
	go func() {
		for {
			line, err := lines.ReadBytes('\n')
			if err != nil {
				close(tn.events)
				return
			}
			var row map[string]any
			if json.Unmarshal(line, &row) != nil {
				continue
			}
			switch {
			case row["settings_request"] != nil:
				q := row["settings_request"].(map[string]any)
				tn.send(t, map[string]any{"settings_reply": map[string]any{"id": q["id"], "session_id": q["session_id"],
					"values": map[string]any{"tts_voice": "alba", "tts_seed": 20261009, "turn_pause_ms": 768}}})
			case row["event"] != nil:
				tn.events <- row["event"].(map[string]any)
			case row["id"] != nil:
				tn.replies <- row
			}
		}
	}()
	return tn
}

func (tn *realTenant) send(t *testing.T, row map[string]any) {
	raw, _ := json.Marshal(row)
	if _, err := tn.process.Stdin.Write(append(raw, '\n')); err != nil {
		t.Errorf("write: %v", err)
	}
}

func (tn *realTenant) call(t *testing.T, id int, op string, args map[string]any) {
	t.Helper()
	tn.send(t, map[string]any{"id": id, "operation": op, "arguments": args})
	select {
	case row := <-tn.replies:
		if row["error"] != nil {
			t.Fatalf("%s: %v", op, row["error"])
		}
	case <-time.After(30 * time.Second):
		t.Fatalf("%s: no answer", op)
	}
}

func (tn *realTenant) await(t *testing.T, kind, session string) map[string]any {
	t.Helper()
	until := time.After(60 * time.Second)
	for {
		select {
		case e, ok := <-tn.events:
			if !ok {
				t.Fatalf("the session process ended before %s", kind)
			}
			if e["type"] == "failure" {
				t.Fatalf("failure before %s: %v", kind, e)
			}
			if e["type"] == kind && e["session_id"] == session {
				return e
			}
		case <-until:
			t.Fatalf("no %s", kind)
		}
	}
}

func TestEngineCoreReal(t *testing.T) {
	root, models := os.Getenv("AII_VOICE_ENGINE_REAL_RUNTIME"), os.Getenv("AII_VOICE_ENGINE_REAL_MODELS")
	if root == "" || models == "" {
		t.Skip("set AII_VOICE_ENGINE_REAL_RUNTIME and AII_VOICE_ENGINE_REAL_MODELS to run the real models through the core")
	}
	worker := filepath.Join(root, "bin", "aii_voice_worker")
	if runtime.GOOS == "windows" {
		worker += ".exe"
	}
	base := workerBase{Path: worker, Args: []string{worker}, Dir: root,
		Env: append(os.Environ(), "AII_MODELS_DIR="+models, defaultLimits.environment())}
	core, err := newEngineCore(base, 180*time.Second, 2*time.Second, 30*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer core.Stop(5 * time.Second)
	t.Logf("model server ready: %s", core.Ready())

	tenants := map[string]*realTenant{}
	order := []string{"A", "B"}
	for _, name := range order {
		pending, err := core.Admit()
		var busy engineBusy
		if errors.As(err, &busy) && name == "B" {
			t.Logf("B refused while A holds its channel (%v): this machine serves one session at a time", err)
			_ = tenants["A"].process.Stdin.Close()
			<-tenants["A"].process.Done()
			delete(tenants, "A")
			for tries := 0; errors.As(err, &busy) && tries < 50; tries++ {
				time.Sleep(100 * time.Millisecond)
				pending, err = core.Admit()
			}
		}
		if err != nil {
			t.Fatalf("%s: admit: %v", name, err)
		}
		tenants[name] = startRealTenant(t, pending, name)
		runRealSession(t, name, tenants[name])
	}
	core.server.control.kill(core.server.cmd)
	for name, tn := range tenants {
		select {
		case <-tn.process.Done():
			t.Logf("%s ended with its server: %v", name, tn.process.Err())
		case <-time.After(10 * time.Second):
			t.Errorf("%s outlived the model server", name)
		}
	}
}

func runRealSession(t *testing.T, name string, tn *realTenant) {
	t.Helper()
	{
		session := "real-" + name
		tn.call(t, 1, "speech.session.open", map[string]any{"session_id": session, "input_handle": session + "-mic",
			"output_handle": session + "-out", "audio": map[string]any{"format": "s16le",
				"input":  map[string]any{"rate": 16000, "channels": 1, "stream": 1001},
				"output": map[string]any{"rate": 24000, "channels": 1}}})
		tn.await(t, "session_ready", session)
		tn.call(t, 2, "speech.session.synthesize", map[string]any{"session_id": session, "synthesis_id": "r-1",
			"text": fmt.Sprintf("Hello %s, the garden is watered.", name)})
		tn.await(t, "synthesis_end", session)
		tn.call(t, 3, "speech.session.close", map[string]any{"session_id": session, "mode": "abort"})
		tn.await(t, "session_end", session)
		t.Logf("%s: session process ready %s; opened, spoke and closed over the shared models", name, tn.ready)
	}
}
