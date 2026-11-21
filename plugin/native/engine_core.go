package main

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

type workerBase struct {
	Path string
	Args []string
	Dir  string
	Env  []string
}

func baseOf(cmd *exec.Cmd) workerBase {
	return workerBase{Path: cmd.Path, Args: append([]string(nil), cmd.Args...), Dir: cmd.Dir, Env: append([]string(nil), cmd.Env...)}
}

func (b workerBase) command(mode string, env ...string) *exec.Cmd {
	cmd := exec.Command(b.Path)
	cmd.Args = append(append([]string(nil), b.Args...), mode)
	cmd.Dir = b.Dir
	for _, entry := range b.Env {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "AII_ENGINE_") || strings.HasPrefix(key, "AII_MODEL_") ||
			key == "AII_AUDIO_IN_FD" || key == "AII_AUDIO_OUT_FD" || key == "AII_VOICE_CARRIER_LIVENESS_FD" {
			continue
		}
		cmd.Env = append(cmd.Env, entry)
	}
	cmd.Env = append(cmd.Env, env...)
	cmd.Stderr = os.Stderr
	return cmd
}

type channelEnd struct{ in, out *os.File }

func (e channelEnd) close() {
	if e.in != nil {
		_ = e.in.Close()
	}
	if e.out != nil {
		_ = e.out.Close()
	}
}

func modelPipes() (session, server channelEnd, err error) {
	upRead, upWrite, err := os.Pipe()
	if err != nil {
		return channelEnd{}, channelEnd{}, err
	}
	downRead, downWrite, err := os.Pipe()
	if err != nil {
		_ = upRead.Close()
		_ = upWrite.Close()
		return channelEnd{}, channelEnd{}, err
	}
	return channelEnd{in: downRead, out: upWrite}, channelEnd{in: upRead, out: downWrite}, nil
}

const (
	frameResult  = 5
	frameAdmit   = 8
	frameRelease = 9
	frameHeader  = 1 + 8 + 8
)

type engineBusy struct {
	RetryAfter time.Duration
	Held       uint32
}

func (b engineBusy) Error() string {
	return fmt.Sprintf("engine busy: %d sessions held; retry after %s", b.Held, b.RetryAfter)
}

var errModelServerLost = errors.New("model server lost")

func askAdmission(end channelEnd, kind byte, within time.Duration) (admitted bool, retryAfter time.Duration, held uint32, err error) {
	type answer struct {
		payload []byte
		err     error
	}
	got := make(chan answer, 1)
	go func() {
		frame := make([]byte, 4+frameHeader)
		binary.NativeEndian.PutUint32(frame, frameHeader)
		frame[4] = kind
		if _, err := end.out.Write(frame); err != nil {
			got <- answer{err: err}
			return
		}
		var length [4]byte
		if _, err := io.ReadFull(end.in, length[:]); err != nil {
			got <- answer{err: err}
			return
		}
		n := binary.NativeEndian.Uint32(length[:])
		if n < frameHeader+1 || n > 1<<16 {
			got <- answer{err: fmt.Errorf("model channel: an answer of %d bytes", n)}
			return
		}
		rest := make([]byte, n)
		if _, err := io.ReadFull(end.in, rest); err != nil {
			got <- answer{err: err}
			return
		}
		if rest[0] != frameResult {
			got <- answer{err: fmt.Errorf("model channel: an answer of kind %d", rest[0])}
			return
		}
		got <- answer{payload: rest[frameHeader:]}
	}()
	var a answer
	select {
	case a = <-got:
	case <-time.After(within):
		return false, 0, 0, fmt.Errorf("model server did not answer within %s", within)
	}
	if a.err != nil {
		return false, 0, 0, fmt.Errorf("%w: %v", errModelServerLost, a.err)
	}
	if a.payload[0] != 0 {
		return false, 0, 0, fmt.Errorf("model server refused the frame: %s", a.payload[1:])
	}
	if kind == frameRelease {
		return false, 0, 0, nil
	}
	body := a.payload[1:]
	if len(body) != 9 {
		return false, 0, 0, fmt.Errorf("model channel: an admission answer of %d bytes", len(body))
	}
	return body[0] != 0, time.Duration(binary.NativeEndian.Uint32(body[1:5])) * time.Millisecond,
		binary.NativeEndian.Uint32(body[5:9]), nil
}

type modelServer struct {
	cmd       *exec.Cmd
	endWithin time.Duration
	control   serverControl
	ready     json.RawMessage
	handing   sync.Mutex
	gone      chan struct{}
	waitErr   error
}

func startModelServer(base workerBase, within, endWithin time.Duration) (*modelServer, error) {
	control, err := newServerControl()
	if err != nil {
		return nil, err
	}
	cmd := base.command("--model-server")
	if err := control.bind(cmd); err != nil {
		control.close()
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		control.close()
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		control.close()
		return nil, err
	}
	control.started(cmd)
	s := &modelServer{cmd: cmd, endWithin: endWithin, control: control, gone: make(chan struct{})}
	line := make(chan []byte, 1)
	go func() {
		reader := bufio.NewReader(stdout)
		first, _ := reader.ReadBytes('\n')
		line <- first
		_, _ = io.Copy(io.Discard, reader)
	}()
	go func() { s.waitErr = cmd.Wait(); close(s.gone) }()
	var first []byte
	select {
	case first = <-line:
	case <-time.After(within):
		s.end()
		return nil, fmt.Errorf("model server not ready within %s", within)
	}
	var said struct {
		Ready json.RawMessage `json:"model_server_ready"`
	}
	if json.Unmarshal(first, &said) != nil || len(said.Ready) == 0 {
		s.end()
		<-s.gone
		return nil, fmt.Errorf("model server did not say it was ready (%v)", s.waitErr)
	}
	s.ready = said.Ready
	return s, nil
}

func (s *modelServer) Lost() <-chan struct{} { return s.gone }

func (s *modelServer) channel(within time.Duration) (channelEnd, error) {
	select {
	case <-s.gone:
		return channelEnd{}, errModelServerLost
	default:
	}
	session, server, err := modelPipes()
	if err != nil {
		return channelEnd{}, err
	}
	served := make(chan error, 1)
	go func() {
		s.handing.Lock()
		defer s.handing.Unlock()
		ok, err := s.control.give(server)
		if err == nil && !ok {
			err = errors.New("model server refused the channel")
		}
		served <- err
	}()
	select {
	case err = <-served:
	case <-time.After(within):
		err = fmt.Errorf("model server did not take a channel within %s", within)
		s.end()
	}
	server.close()
	if err != nil {
		session.close()
		return channelEnd{}, err
	}
	return session, nil
}

func (s *modelServer) end() {
	s.control.close()
	select {
	case <-s.gone:
	case <-time.After(s.endWithin):
		s.control.kill(s.cmd)
		<-s.gone
	}
}

type sessionProcess struct {
	cmd    *exec.Cmd
	Stdin  io.WriteCloser
	Stdout io.ReadCloser
	ended  sessionEnding
	done   chan struct{}
	err    error
}

func startSessionProcess(base workerBase, model channelEnd, audioIn, audioOut *os.File) (*sessionProcess, error) {
	defer model.close()
	defer audioIn.Close()
	defer audioOut.Close()
	cmd := base.command("--session")
	ending, err := bindSession(cmd, model, audioIn, audioOut)
	if err != nil {
		return nil, err
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		ending.close()
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		ending.close()
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		ending.close()
		return nil, err
	}
	ending.started(cmd)
	p := &sessionProcess{cmd: cmd, Stdin: stdin, Stdout: stdout, ended: ending, done: make(chan struct{})}
	go func() { p.err = cmd.Wait(); ending.exited(); close(p.done) }()
	return p, nil
}

func (p *sessionProcess) Done() <-chan struct{} { return p.done }

func (p *sessionProcess) Err() error { return p.err }

func (p *sessionProcess) Kill() { p.ended.kill(p.cmd) }

type engineCore struct {
	base       workerBase
	server     *modelServer
	openWithin time.Duration

	mu       sync.Mutex
	live     map[*sessionProcess]struct{}
	stopping bool
}

var errEngineStopping = errors.New("engine stopping: it takes no new channel")

func newEngineCore(base workerBase, readyWithin, openWithin, endWithin time.Duration) (*engineCore, error) {
	server, err := startModelServer(base, readyWithin, endWithin)
	if err != nil {
		return nil, err
	}
	return &engineCore{base: base, server: server, openWithin: openWithin, live: map[*sessionProcess]struct{}{}}, nil
}

func (e *engineCore) Ready() json.RawMessage { return e.server.ready }

func (e *engineCore) Lost() <-chan struct{} { return e.server.Lost() }

type pendingChannel struct {
	core *engineCore
	end  channelEnd
}

func (e *engineCore) Admit() (*pendingChannel, error) {
	e.mu.Lock()
	stopping := e.stopping
	e.mu.Unlock()
	if stopping {
		return nil, errEngineStopping
	}
	end, err := e.server.channel(e.openWithin)
	if err != nil {
		return nil, err
	}
	admitted, retryAfter, held, err := askAdmission(end, frameAdmit, e.openWithin)
	if err != nil {
		end.close()
		return nil, err
	}
	if !admitted {
		end.close()
		return nil, engineBusy{RetryAfter: retryAfter, Held: held}
	}
	return &pendingChannel{core: e, end: end}, nil
}

func (p *pendingChannel) Cancel() { p.end.close() }

func (p *pendingChannel) Start(audioIn, audioOut *os.File) (*sessionProcess, error) {
	e := p.core
	e.mu.Lock()
	if e.stopping {
		e.mu.Unlock()
		p.end.close()
		_ = audioIn.Close()
		_ = audioOut.Close()
		return nil, errEngineStopping
	}
	e.mu.Unlock()
	process, err := startSessionProcess(e.base, p.end, audioIn, audioOut)
	if err != nil {
		return nil, err
	}
	e.mu.Lock()
	e.live[process] = struct{}{}
	e.mu.Unlock()
	go func() {
		<-process.Done()
		e.mu.Lock()
		delete(e.live, process)
		e.mu.Unlock()
	}()
	return process, nil
}

func (e *engineCore) Live() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.live)
}

func (e *engineCore) Stop(within time.Duration) {
	e.mu.Lock()
	e.stopping = true
	e.mu.Unlock()
	e.mu.Lock()
	left := make([]*sessionProcess, 0, len(e.live))
	for p := range e.live {
		left = append(left, p)
	}
	e.mu.Unlock()
	deadline := time.After(within)
	for _, p := range left {
		select {
		case <-p.Done():
		case <-deadline:
			p.Kill()
			<-p.Done()
		}
	}
	e.server.end()
}
