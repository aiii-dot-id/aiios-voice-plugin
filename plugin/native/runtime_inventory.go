package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"time"
)

type runtimeJob struct {
	path, name string
	entry      fs.DirEntry
	item       runtimeFile
}

// verifyRuntimeFile preserves the original metadata/content checks and binds
// the opened handle's size/mode too. A separate reusable buffer belongs to each
// worker; wrapping Reader prevents File.WriteTo from allocating another buffer.
func verifyRuntimeFile(job runtimeJob, buffer []byte) error {
	info, err := job.entry.Info()
	if err != nil {
		return err
	}
	checkInfo := func(info fs.FileInfo) error {
		if !info.Mode().IsRegular() {
			return fmt.Errorf("non-regular runtime file: %s", job.name)
		}
		modeMismatch := runtime.GOOS != "windows" && (info.Mode()&0111 != 0) != job.item.Executable
		if info.Size() != job.item.Bytes || modeMismatch {
			return fmt.Errorf("runtime inventory mismatch: %s", job.name)
		}
		return nil
	}
	if err = checkInfo(info); err != nil {
		return err
	}
	f, err := os.Open(job.path)
	if err != nil {
		return err
	}
	actual, err := f.Stat()
	if err == nil {
		err = checkInfo(actual)
	}
	if err != nil {
		return errors.Join(err, f.Close())
	}
	h := sha256.New()
	n, copyErr := io.CopyBuffer(h, struct{ io.Reader }{f}, buffer)
	closeErr := f.Close()
	if err = errors.Join(copyErr, closeErr); err != nil {
		return err
	}
	if n != job.item.Bytes || hex.EncodeToString(h.Sum(nil)) != job.item.SHA256 {
		return fmt.Errorf("runtime hash mismatch: %s", job.name)
	}
	return nil
}

// A per-call checker makes blocked I/O, read failure and retirement testable
// without a global mutable hook. Production always supplies verifyRuntimeFile.
// Map and entries are read-only. No profile or readiness escapes before join.
func verifyInventory(root, carrierName string, files map[string]runtimeFile, workers int,
	check func(runtimeJob, []byte) error) (int, int64, error) {
	if workers < 1 || workers > 8 || check == nil {
		return 0, 0, errors.New("invalid runtime verifier worker count")
	}
	jobs := make(chan runtimeJob, workers*2)
	stop := make(chan struct{})
	var once sync.Once
	var failure error
	fail := func(err error) {
		if err != nil {
			once.Do(func() { failure = err; close(stop) })
		}
	}
	var wg sync.WaitGroup
	var mu sync.Mutex
	seen := 0
	var verifiedBytes int64
	lastProgress := time.Now()
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buffer := make([]byte, 128*1024)
			for job := range jobs {
				select {
				case <-stop:
					return
				default:
				}
				if err := check(job, buffer); err != nil {
					fail(err)
					return
				}
				mu.Lock()
				seen++
				verifiedBytes += job.item.Bytes
				if seen%1024 == 0 || time.Since(lastProgress) >= 2*time.Second {
					startupPhase("runtime-verification-progress", seen, verifiedBytes)
					lastProgress = time.Now()
				}
				mu.Unlock()
			}
		}()
	}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		select {
		case <-stop:
			return fs.SkipAll
		default:
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("runtime symlink refused: %s", path)
		}
		if entry.IsDir() {
			return nil
		}
		name, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		name = filepath.ToSlash(name)
		if name == "voice-runtime.json" || name == carrierName {
			info, err := entry.Info()
			if err != nil {
				return err
			}
			if !info.Mode().IsRegular() {
				return fmt.Errorf("non-regular runtime file: %s", name)
			}
			return nil
		}
		item, ok := files[name]
		if !ok {
			return fmt.Errorf("runtime inventory mismatch: %s", name)
		}
		select {
		case jobs <- runtimeJob{path, name, entry, item}:
			return nil
		case <-stop:
			return fs.SkipAll
		}
	})
	fail(err)
	close(jobs)
	wg.Wait()
	if failure != nil {
		return seen, verifiedBytes, failure
	}
	if seen != len(files) {
		return seen, verifiedBytes, errors.New("runtime inventory incomplete")
	}
	return seen, verifiedBytes, nil
}
