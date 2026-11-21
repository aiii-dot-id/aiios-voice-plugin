package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

type serverControl struct {
	toServer, fromServer      *os.File
	serverReads, serverWrites *os.File
	process                   syscall.Handle
}

const processDupHandle = 0x0040

func newServerControl() (serverControl, error) {
	serverReads, toServer, err := os.Pipe()
	if err != nil {
		return serverControl{}, err
	}
	fromServer, serverWrites, err := os.Pipe()
	if err != nil {
		_ = serverReads.Close()
		_ = toServer.Close()
		return serverControl{}, err
	}
	return serverControl{toServer: toServer, fromServer: fromServer, serverReads: serverReads, serverWrites: serverWrites}, nil
}

func (c *serverControl) bind(cmd *exec.Cmd) error {
	if workerTreeJob == 0 {
		if err := ownWorkerTree(); err != nil {
			return err
		}
	}
	handles, err := inheritable(c.serverReads, c.serverWrites)
	if err != nil {
		return err
	}
	cmd.Env = append(cmd.Env, "AII_MODEL_CONTROL_IN="+strconv.FormatUint(uint64(handles[0]), 10),
		"AII_MODEL_CONTROL_OUT="+strconv.FormatUint(uint64(handles[1]), 10))
	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles}
	return nil
}

func (c *serverControl) started(cmd *exec.Cmd) {
	for _, h := range cmd.SysProcAttr.AdditionalInheritedHandles {
		_ = syscall.CloseHandle(h)
	}
	_ = c.serverReads.Close()
	_ = c.serverWrites.Close()
	if h, err := syscall.OpenProcess(processDupHandle, false, uint32(cmd.Process.Pid)); err == nil {
		c.process = h
	}
}

func (c *serverControl) give(end channelEnd) (bool, error) {
	if c.process == 0 {
		return false, errors.New("model server: no handle to give a channel into")
	}
	self, err := syscall.GetCurrentProcess()
	if err != nil {
		return false, err
	}
	var values [16]byte
	for i, f := range []*os.File{end.in, end.out} {
		var theirs syscall.Handle
		if err := syscall.DuplicateHandle(self, syscall.Handle(f.Fd()), c.process, &theirs, 0, false, syscall.DUPLICATE_SAME_ACCESS); err != nil {
			return false, fmt.Errorf("model server: the channel's handles cannot be given to it: %w", err)
		}
		binary.LittleEndian.PutUint64(values[8*i:], uint64(theirs))
	}
	if _, err := c.toServer.Write(values[:]); err != nil {
		return false, fmt.Errorf("%w: %v", errModelServerLost, err)
	}
	word := make([]byte, 1)
	if n, err := c.fromServer.Read(word); n != 1 || err != nil {
		return false, fmt.Errorf("%w: its control ended", errModelServerLost)
	}
	switch word[0] {
	case 'y':
		return true, nil
	case 'n':
		return false, nil
	}
	return false, errors.New("model server answered neither y nor n")
}

func (c *serverControl) close() {
	_ = c.toServer.Close()
	_ = c.fromServer.Close()
	if c.serverReads != nil {
		_ = c.serverReads.Close()
		_ = c.serverWrites.Close()
	}
}

func (c *serverControl) kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
	if c.process != 0 {
		_ = syscall.CloseHandle(c.process)
		c.process = 0
	}
}

type sessionEnding struct {
	handles []syscall.Handle
}

func bindSession(cmd *exec.Cmd, model channelEnd, audioIn, audioOut *os.File) (sessionEnding, error) {
	if workerTreeJob == 0 {
		if err := ownWorkerTree(); err != nil {
			return sessionEnding{}, err
		}
	}
	handles, err := inheritable(audioIn, audioOut, model.in, model.out)
	if err != nil {
		return sessionEnding{}, err
	}
	value := func(h syscall.Handle) string { return strconv.FormatUint(uint64(h), 10) }
	cmd.Env = append(cmd.Env, "AII_AUDIO_IN_FD="+value(handles[0]), "AII_AUDIO_OUT_FD="+value(handles[1]),
		"AII_MODEL_CHANNEL_IN="+value(handles[2]), "AII_MODEL_CHANNEL_OUT="+value(handles[3]))
	cmd.SysProcAttr = &syscall.SysProcAttr{AdditionalInheritedHandles: handles}
	return sessionEnding{handles: handles}, nil
}

func (e sessionEnding) started(*exec.Cmd) { e.close() }

func (e sessionEnding) exited() {}

func (e sessionEnding) close() {
	for _, h := range e.handles {
		_ = syscall.CloseHandle(h)
	}
}

func (e sessionEnding) kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}

func inheritable(files ...*os.File) ([]syscall.Handle, error) {
	self, err := syscall.GetCurrentProcess()
	if err != nil {
		return nil, err
	}
	var handles []syscall.Handle
	for _, f := range files {
		var h syscall.Handle
		if err := syscall.DuplicateHandle(self, syscall.Handle(f.Fd()), self, &h, 0, true, syscall.DUPLICATE_SAME_ACCESS); err != nil {
			for _, made := range handles {
				_ = syscall.CloseHandle(made)
			}
			return nil, err
		}
		handles = append(handles, h)
	}
	return handles, nil
}
