//go:build darwin || linux

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
)

type serverControl struct {
	mine, theirs *os.File
}

const internalFDsEnv = "AII_ENGINE_INTERNAL_FDS"

func newServerControl() (serverControl, error) {
	if value := os.Getenv(internalFDsEnv); value != "" {
		a, b, ok := strings.Cut(value, ",")
		fa, errA := strconv.Atoi(a)
		fb, errB := strconv.Atoi(b)
		if !ok || errA != nil || errB != nil || fa < 3 || fb < 3 || fa == fb {
			return serverControl{}, fmt.Errorf("invalid %s", internalFDsEnv)
		}
		for _, fd := range []int{fa, fb} {
			var st syscall.Stat_t
			if syscall.Fstat(fd, &st) != nil || st.Mode&syscall.S_IFMT != syscall.S_IFSOCK {
				return serverControl{}, fmt.Errorf("%s names a descriptor that is not a socket", internalFDsEnv)
			}
			syscall.CloseOnExec(fd)
		}
		return serverControl{mine: os.NewFile(uintptr(fa), "engine-internal"), theirs: os.NewFile(uintptr(fb), "engine-internal-server")}, nil
	}
	syscall.ForkLock.RLock()
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err == nil {
		syscall.CloseOnExec(fds[0])
		syscall.CloseOnExec(fds[1])
	}
	syscall.ForkLock.RUnlock()
	if err != nil {
		return serverControl{}, err
	}
	return serverControl{mine: os.NewFile(uintptr(fds[0]), "model-control"), theirs: os.NewFile(uintptr(fds[1]), "model-control-server")}, nil
}

func (c serverControl) bind(cmd *exec.Cmd) error {
	cmd.ExtraFiles = []*os.File{c.theirs}
	cmd.Env = append(cmd.Env, "AII_MODEL_CONTROL_FD=3")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return nil
}

func (c serverControl) started(*exec.Cmd) { _ = c.theirs.Close() }

func (c serverControl) give(end channelEnd) (bool, error) {
	rights := syscall.UnixRights(int(end.in.Fd()), int(end.out.Fd()))
	raw, err := c.mine.SyscallConn()
	if err != nil {
		return false, err
	}
	var sendErr error
	if err := raw.Write(func(fd uintptr) bool {
		sendErr = syscall.Sendmsg(int(fd), []byte{'c'}, rights, nil, 0)
		return sendErr != syscall.EAGAIN
	}); err != nil {
		return false, err
	}
	if sendErr != nil {
		return false, fmt.Errorf("%w: %v", errModelServerLost, sendErr)
	}
	word := make([]byte, 1)
	if n, err := c.mine.Read(word); n != 1 || err != nil {
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

func (c serverControl) close() {
	_ = c.mine.Close()
	_ = c.theirs.Close()
}

func (c serverControl) kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}

type sessionEnding struct {
	liveness, livenessRead *os.File
}

func bindSession(cmd *exec.Cmd, model channelEnd, audioIn, audioOut *os.File) (sessionEnding, error) {
	read, write, err := os.Pipe()
	if err != nil {
		return sessionEnding{}, err
	}
	cmd.ExtraFiles = []*os.File{audioIn, audioOut, read, model.in, model.out}
	cmd.Env = append(cmd.Env, "AII_AUDIO_IN_FD=3", "AII_AUDIO_OUT_FD=4", "AII_VOICE_CARRIER_LIVENESS_FD=5",
		"AII_MODEL_CHANNEL_IN=6", "AII_MODEL_CHANNEL_OUT=7")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return sessionEnding{liveness: write, livenessRead: read}, nil
}

func (e sessionEnding) started(*exec.Cmd) { _ = e.livenessRead.Close() }

func (e sessionEnding) exited() { _ = e.liveness.Close() }

func (e sessionEnding) close() {
	_ = e.liveness.Close()
	_ = e.livenessRead.Close()
}

func (e sessionEnding) kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}
