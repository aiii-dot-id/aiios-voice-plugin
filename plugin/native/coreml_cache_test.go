package main

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestPackagedCoreMLCacheBinding(t *testing.T) {
	for _, mutation := range []string{"valid", "missing", "changed", "unbound", "outside"} {
		t.Run(mutation, func(t *testing.T) {
			root, p := nativeRuntimeFixture(t, []byte("worker"))
			p.CoreMLCache = "coreml-cache"
			name := "coreml-cache/compiled_model.mlmodelc/model.bin"
			file := filepath.Join(root, filepath.FromSlash(name))
			if err := os.MkdirAll(filepath.Dir(file), 0755); err != nil {
				t.Fatal(err)
			}
			data := []byte("compiled model")
			if err := os.WriteFile(file, data, 0644); err != nil {
				t.Fatal(err)
			}
			h := sha256.Sum256(data)
			p.Files[name] = runtimeFile{hex.EncodeToString(h[:]), int64(len(data)), false}
			if mutation == "unbound" {
				delete(p.Files, name)
			}
			if mutation == "outside" {
				p.CoreMLCache = "../cache"
			}
			digest := writeRuntimeProfile(t, root, p)
			if mutation == "missing" {
				if err := os.Remove(file); err != nil {
					t.Fatal(err)
				}
			}
			if mutation == "changed" {
				if err := os.WriteFile(file, []byte("tampered model"), 0644); err != nil {
					t.Fatal(err)
				}
			}
			t.Setenv("AII_MODELS_DIR", t.TempDir())
			t.Setenv("AII_VOICE_COREML_CACHE_DIR", "/unbound/cache")
			cmd, err := packagedCommand(filepath.Join(root, "carrier"), digest)
			if mutation != "valid" || runtime.GOOS != "darwin" {
				if err == nil {
					t.Fatal("invalid or non-Mac cache accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			seen := 0
			for _, entry := range cmd.Env {
				if strings.HasPrefix(entry, "AII_VOICE_COREML_CACHE_DIR=") {
					seen++
					path := strings.TrimPrefix(entry, "AII_VOICE_COREML_CACHE_DIR=")
					a, e1 := os.Stat(path)
					b, e2 := os.Stat(filepath.Join(root, p.CoreMLCache))
					if e1 != nil || e2 != nil || !os.SameFile(a, b) {
						t.Fatal("unbound cache escaped")
					}
				}
			}
			if seen != 1 {
				t.Fatalf("cache binding count %d", seen)
			}
		})
	}
}
