//go:build darwin || linux

package main

import (
	"os"
	"os/exec"
)

func handAudio(cmd *exec.Cmd, in, out *os.File) {
	cmd.ExtraFiles = []*os.File{in, out}
	cmd.Env = append(cmd.Env, "AII_AUDIO_IN_FD=3", "AII_AUDIO_OUT_FD=4")
}

func passInherited(cmd *exec.Cmd) []*os.File {
	cmd.ExtraFiles = []*os.File{os.NewFile(3, "audio-in"), os.NewFile(4, "audio-out"), os.NewFile(5, "carrier-liveness")}
	return cmd.ExtraFiles
}
