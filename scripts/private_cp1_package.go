// Private artifact assembler using the unmodified public SDK. No signing.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	pkg "github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiospkg"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func read(path string) []byte { b, e := os.ReadFile(path); must(e); return b }
func write(path string, b []byte) {
	must(os.MkdirAll(filepath.Dir(path), 0755))
	f, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	must(e)
	_, e = f.Write(b)
	must(e)
	must(f.Close())
}

// A named schema is a dependency, not optional documentation. Keep it in
// the same hash-bound install map as the interface that references it.
func addSchemas(files map[string][]byte, descriptors []byte, root *os.Root) error {
	var operations []struct {
		ID, Summary, Input, Output string
		Examples                   []string
	}
	if err := json.Unmarshal(descriptors, &operations); err != nil {
		return err
	}
	seen := map[string]bool{}
	for _, op := range operations {
		if strings.HasPrefix(op.ID, "speaker.") {
			if strings.TrimSpace(op.Summary) == "" || op.Input == "" || op.Output == "" || len(op.Examples) == 0 {
				return fmt.Errorf("AI-visible speaker contract incomplete: %s needs summary, input, output and examples", op.ID)
			}
			for _, example := range op.Examples {
				var value map[string]json.RawMessage
				if json.Unmarshal([]byte(example), &value) != nil || value == nil {
					return fmt.Errorf("AI-visible speaker example must be a JSON argument object: %s", op.ID)
				}
			}
		}
		for _, ref := range []string{op.Input, op.Output} {
			if ref == "" || seen[ref] {
				continue
			}
			if !filepath.IsLocal(ref) || strings.Contains(ref, `\`) || !strings.HasPrefix(ref, "schemas/") {
				return fmt.Errorf("unsafe operation schema path %q", ref)
			}
			if _, collision := files[ref]; collision {
				return fmt.Errorf("operation schema collides with install member %q", ref)
			}
			raw, err := root.ReadFile(ref)
			if err != nil {
				return fmt.Errorf("declared operation schema %q missing or unreadable: %w", ref, err)
			}
			if err := pkg.CheckSchemaSubset(raw); err != nil {
				return fmt.Errorf("declared operation schema %q: %w", ref, err)
			}
			files[ref] = raw
			seen[ref] = true
		}
		if strings.HasPrefix(op.ID, "speaker.") {
			var schema struct {
				Type, Description string
				Properties        map[string]struct{ Type, Description string }
				Required          []string
			}
			if json.Unmarshal(files[op.Input], &schema) != nil || schema.Type != "object" ||
				strings.TrimSpace(schema.Description) == "" || schema.Properties == nil {
				return fmt.Errorf("AI-visible speaker input must describe its object arguments: %s", op.ID)
			}
			for key, property := range schema.Properties {
				if property.Type == "" || strings.TrimSpace(property.Description) == "" {
					return fmt.Errorf("AI-visible speaker parameter %s.%s needs type and description", op.ID, key)
				}
			}
			for _, required := range schema.Required {
				if _, ok := schema.Properties[required]; !ok {
					return fmt.Errorf("AI-visible speaker required parameter %s.%s is undeclared", op.ID, required)
				}
			}
			for _, example := range op.Examples {
				var value map[string]json.RawMessage
				_ = json.Unmarshal([]byte(example), &value) // object checked above
				for _, key := range schema.Required {
					if _, ok := value[key]; !ok {
						return fmt.Errorf("speaker example for %s omits required %s", op.ID, key)
					}
				}
				for key := range value {
					if _, ok := schema.Properties[key]; !ok {
						return fmt.Errorf("speaker example for %s invents parameter %s", op.ID, key)
					}
				}
			}
		}
	}
	return nil
}

// This is an optional authoring input, not a plugin/runtime API. Every listed
// notice becomes an ordinary hash-bound package member. Existing checkpoints
// without this input retain exactly the same package bytes.
func addNotices(files map[string][]byte, root *os.Root) error {
	info, err := root.Lstat("release-notices.json")
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() > 1<<20 {
		return fmt.Errorf("notice declaration must be a bounded regular file")
	}
	raw, err := root.ReadFile("release-notices.json")
	if err != nil {
		return err
	}
	var rows []struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
		Size   int64  `json:"size"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&rows); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("notice declaration has trailing data")
	}
	if len(rows) == 0 || len(rows) > 256 {
		return fmt.Errorf("notice declaration count outside 1..256")
	}
	for _, row := range rows {
		if !filepath.IsLocal(row.Path) || path.Clean(row.Path) != row.Path || strings.Contains(row.Path, `\`) || !strings.HasPrefix(row.Path, "notices/") {
			return fmt.Errorf("unsafe notice path %q", row.Path)
		}
		if _, exists := files[row.Path]; exists {
			return fmt.Errorf("duplicate or colliding notice %q", row.Path)
		}
		info, err := root.Lstat(row.Path)
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() || row.Size < 1 || row.Size > 16<<20 || info.Size() != row.Size {
			return fmt.Errorf("notice size or kind differs: %q", row.Path)
		}
		b, err := root.ReadFile(row.Path)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(b)
		if int64(len(b)) != row.Size || hex.EncodeToString(sum[:]) != row.SHA256 {
			return fmt.Errorf("notice hash or size differs: %q", row.Path)
		}
		files[row.Path] = b
	}
	return nil
}

func main() {
	if len(os.Args) != 2 {
		panic("fresh signing-input directory required")
	}
	dir := os.Args[1]
	cfg, e := pkg.LoadAuthorConfig(filepath.Join(dir, "plugin.json"))
	must(e)
	desc := read(filepath.Join(dir, "descriptors.json"))
	methods, e := pkg.DescriptorIDs(desc)
	must(e)
	ifaces := cfg.InterfaceList()
	partition, e := pkg.PartitionMethods(ifaces, methods)
	must(e)
	files := map[string][]byte{}
	root, e := os.OpenRoot(dir)
	must(e)
	defer root.Close()
	for _, variant := range cfg.Variants {
		artifact := variant.Artifact
		// Keep historical single-platform checkpoint bytes unchanged. A family
		// must explicitly bind every entrypoint; never copy the first into all.
		if artifact == "" {
			if len(cfg.Variants) != 1 {
				panic("each family variant requires its own artifact: " + variant.VariantID)
			}
			artifact = "aii-voice-t3"
		}
		b, e := root.ReadFile(artifact)
		must(e)
		files[pkg.EntrypointRel(variant)] = b
	}
	for _, iface := range ifaces {
		schema := desc // Preserve historical one-interface bytes exactly.
		if len(ifaces) > 1 {
			schema, e = pkg.DescriptorsSubset(desc, partition[iface.ID])
			must(e)
		}
		files[pkg.SchemaFileRel(iface)] = schema
	}
	must(addSchemas(files, desc, root))
	a, present, e := pkg.AcceleratorJSON(cfg.Variants)
	must(e)
	if !present {
		panic("accelerator binding absent")
	}
	files[pkg.AcceleratorFile] = a
	m, e := pkg.ModelsJSON(cfg.Models)
	must(e)
	files[pkg.ModelsFile] = m
	must(pkg.ValidateRuntimes(cfg.Runtimes, cfg.Variants))
	r, e := pkg.RuntimesJSON(cfg.Runtimes)
	must(e)
	files[pkg.RuntimesFile] = r
	// A declaration must be validated and hashed into the bundle, not merely
	// present in its author JSON. Existing settings-free checkpoints stay exact.
	if len(cfg.Settings) > 0 {
		s, e := pkg.SettingsJSON(cfg.Settings)
		must(e)
		files[pkg.SettingsFile] = s
	}
	must(addNotices(files, root))
	manifest, e := pkg.BuildManifest(cfg, methods, files)
	must(e)
	tree := pkg.NewTree(cfg.Root())
	tree.Add("manifest.json", manifest)
	for name, b := range files {
		tree.Add("install-root/"+name, b)
	}
	bundle, e := pkg.WriteBundle(tree)
	must(e)
	again, e := pkg.WriteBundle(tree)
	must(e)
	if string(bundle) != string(again) {
		panic("canonical reassembly differs")
	}
	for name, b := range tree.Files {
		write(filepath.Join(dir, "stage", cfg.Root(), name), b)
	}
	name := cfg.Root() + ".aiiospkg"
	write(filepath.Join(dir, name), bundle)
	sum := sha256.Sum256(bundle)
	mh, e := pkg.ManifestHash(manifest)
	must(e)
	ceilings := map[string]any{}
	for _, runtime := range cfg.Runtimes {
		ceilings[runtime.VariantID] = pkg.DeclaredCeilings(runtime)
	}
	out := map[string]any{"bundle": name, "bytes": len(bundle), "sha256": hex.EncodeToString(sum[:]), "package_hash": pkg.PackageHash(files), "manifest_hash": mh, "methods": methods, "unsigned": true, "canonical_reassembly_equal": true, "runtime_ceilings_by_variant": ceilings}
	if len(cfg.Runtimes) == 1 {
		out["required_runtime_ceiling_overrides"] = pkg.DeclaredCeilings(cfg.Runtimes[0])
	}
	b, e := json.MarshalIndent(out, "", "  ")
	must(e)
	fmt.Println(string(b))
}
