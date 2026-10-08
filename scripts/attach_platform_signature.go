// Attach an independently signed platform envelope to an exact SDK bundle.
// This tool never handles a private key. Host verification remains mandatory.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	pkg "github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiospkg"
)

// A staged root is a package's own name: its id, a hyphen, its version. The
// tool took only this plugin's id, and so could not attach a signature to
// another plugin's package that the same platform key signs. Nothing else
// here ever depended on the id: the staged tree must be the unsigned bundle
// byte for byte, and the envelope must sign that package and that manifest.
var packageRoot = regexp.MustCompile(`^id\.aiii\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*-[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$`)

func attach(stage, unsigned, signature, output string) error {
	stage, err := filepath.Abs(stage)
	if err != nil {
		return err
	}
	root := filepath.Base(stage)
	if !packageRoot.MatchString(root) {
		return errors.New("unexpected package root")
	}
	if filepath.Base(unsigned) != root+".aiiospkg" || filepath.Base(output) != root+".aiiospkg" {
		return errors.New("package name differs from staged root")
	}
	tree := pkg.NewTree(root)
	err = filepath.WalkDir(stage, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == stage {
			return nil
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("non-regular staged member: %s", path)
		}
		rel, err := filepath.Rel(stage, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if rel != "manifest.json" && !strings.HasPrefix(rel, "install-root/") {
			return fmt.Errorf("unexpected staged member: %s", rel)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		tree.Add(rel, data)
		return nil
	})
	if err != nil {
		return err
	}
	if len(tree.Files) < 2 || tree.Files["manifest.json"] == nil {
		return errors.New("incomplete staged tree")
	}
	prior, err := pkg.WriteBundle(tree)
	if err != nil {
		return err
	}
	original, err := os.ReadFile(unsigned)
	if err != nil {
		return err
	}
	if !bytes.Equal(prior, original) {
		return errors.New("staged tree differs from unsigned bundle")
	}
	install := map[string][]byte{}
	for name, data := range tree.Files {
		if strings.HasPrefix(name, "install-root/") {
			install[strings.TrimPrefix(name, "install-root/")] = data
		}
	}
	manifestHash, err := pkg.ManifestHash(tree.Files["manifest.json"])
	if err != nil {
		return err
	}
	wantPackage := pkg.PackageHash(install)
	sig, err := os.ReadFile(signature)
	if err != nil {
		return err
	}
	if len(sig) == 0 || len(sig) > 1<<20 {
		return errors.New("signature size outside limit")
	}
	var envelope struct {
		ArtifactKind string `json:"artifact_kind"`
		Payload      struct {
			PackageHash  string `json:"package_hash"`
			ManifestHash string `json:"manifest_hash"`
		} `json:"payload"`
	}
	if err := json.Unmarshal(sig, &envelope); err != nil {
		return err
	}
	if envelope.ArtifactKind != "plugin.platform_release" || envelope.Payload.PackageHash != wantPackage || envelope.Payload.ManifestHash != manifestHash {
		return errors.New("platform envelope signs a different package or manifest")
	}
	tree.Add("signatures/platform.sig", sig)
	signed, err := pkg.WriteBundle(tree)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	if _, err = file.Write(signed); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}

func main() {
	if len(os.Args) != 5 {
		fmt.Fprintln(os.Stderr, "usage: attach-platform-signature <stage-root> <unsigned.aiiospkg> <platform.sig> <output.aiiospkg>")
		os.Exit(2)
	}
	if err := attach(os.Args[1], os.Args[2], os.Args[3], os.Args[4]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
