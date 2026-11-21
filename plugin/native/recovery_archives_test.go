package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"sort"
	"strings"
	"testing"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

type archiveStorage struct {
	files map[string]string
	links map[string]bool
	calls []string
}

func (f *archiveStorage) HostCallTo(_ context.Context, operation string, target any, args any) (aiiosdk.Object, error) {
	name, ok := target.(map[string]any)
	if !ok || name["root"] != "private" {
		return nil, errors.New("foreign private root")
	}
	path := name["path"].(string)
	f.calls = append(f.calls, operation+":"+path)
	result := map[string]any{"root": "private", "path": path}
	switch operation {
	case "fs.list":
		rows := make([]recordingEntry, 0)
		for file, data := range f.files {
			if strings.HasPrefix(file, path+"/") {
				size := int64(len(data))
				rows = append(rows, recordingEntry{Name: strings.TrimPrefix(file, path+"/"), Size: &size, Symlink: f.links[file], Modified: "2026-10-24T08:00:00Z"})
			}
		}
		sort.Slice(rows, func(i, j int) bool { return rows[i].Name < rows[j].Name })
		result["entries"], result["truncated"] = rows, false
	case "fs.read":
		data, present := f.files[path]
		if !present {
			return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_NOT_FOUND"}`), nil
		}
		if f.links[path] {
			return aiiosdk.Object(`{"status":"denied","reasonCode":"FS_SYMLINK_REFUSED"}`), nil
		}
		n := int(args.(map[string]any)["length"].(int))
		if n > len(data) {
			n = len(data)
		}
		result["size"], result["offset"], result["bytes"], result["data_b64"] = len(data), 0, n, base64.StdEncoding.EncodeToString([]byte(data[:n]))
	case "fs.delete":
		_, present := f.files[path]
		delete(f.files, path)
		result["deleted"] = present
	default:
		return nil, errors.New("unexpected host operation")
	}
	raw, err := json.Marshal(map[string]any{"status": "succeeded", "operation_result": result})
	return aiiosdk.Object(raw), err
}

func TestRecoveryArchivesAreListedAndDeleted(t *testing.T) {
	speakers, registry := strings.Repeat("a", 64), strings.Repeat("b", 64)
	f := &archiveStorage{files: map[string]string{
		"uid/recovery-" + speakers + ".json":                `{"enrollment_sha256":"` + strings.Repeat("1", 64) + `","captures_sha256":"absent"}`,
		"uid/recovery-" + registry + ".json":                `{"speaker_registry_sha256":"` + strings.Repeat("2", 64) + `"}`,
		"uid/enrollment.json":                               `{}`,
		"uid/speakers.json":                                 `{}`,
		"uid/.recovery-" + speakers + ".pending":            `{}`,
		"uid/recovery-" + strings.Repeat("c", 64) + ".json": `{}`,
		"uid/recovery-XYZ.json":                             `{}`,
	}, links: map[string]bool{"uid/recovery-" + strings.Repeat("c", 64) + ".json": true}}
	listed, err := recoveryArchiveCall(context.Background(), f, "speaker.recovery_list", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	result := listed.(map[string]any)["operation_result"].(map[string]any)
	archives := result["archives"].([]map[string]any)
	if len(archives) != 2 || archives[0]["archive_sha256"] != speakers || archives[0]["holds"] != "enrolled_speakers_and_captures" ||
		archives[1]["archive_sha256"] != registry || archives[1]["holds"] != "speaker_registry" || archives[0]["saved_at"] != "2026-10-24T08:00:00Z" {
		t.Fatalf("the archives, or what they hold, were not listed: %v", archives)
	}
	for _, row := range archives {
		for key := range row {
			if key != "archive_sha256" && key != "bytes" && key != "holds" && key != "saved_at" {
				t.Fatalf("a listing returned %s", key)
			}
		}
	}
	deleted, err := recoveryArchiveCall(context.Background(), f, "speaker.recovery_delete", aiiosdk.Object(`{"archive_sha256":"`+registry+`"}`))
	if err != nil {
		t.Fatal(err)
	}
	if got := deleted.(map[string]any)["operation_result"].(map[string]any); got["deleted"] != true || got["archive_sha256"] != registry {
		t.Fatalf("delete receipt: %v", got)
	}
	if _, left := f.files["uid/recovery-"+registry+".json"]; left {
		t.Fatal("the archive is still stored")
	}
	if _, kept := f.files["uid/recovery-"+speakers+".json"]; !kept || f.files["uid/speakers.json"] == "" {
		t.Fatal("deleting one archive touched another file")
	}
	again, err := recoveryArchiveCall(context.Background(), f, "speaker.recovery_delete", aiiosdk.Object(`{"archive_sha256":"`+registry+`"}`))
	if err != nil || again.(map[string]any)["operation_result"].(map[string]any)["deleted"] != false {
		t.Fatalf("an absent archive was not reported absent: %v %v", again, err)
	}
	if _, err := recoveryArchiveCall(context.Background(), f, "speaker.recovery_delete", aiiosdk.Object(`{"archive_sha256":"`+strings.Repeat("c", 64)+`"}`)); err == nil {
		t.Fatal("a link was deleted as an archive")
	}
}

func TestRecoveryArchiveArgumentsAndConfirmation(t *testing.T) {
	id := strings.Repeat("d", 64)
	confirmed := func(extra map[string]any) aiiosdk.Object {
		a := hostEnrollmentArgs("speaker.forget")
		delete(a, "session_id")
		for k, v := range extra {
			a[k] = v
		}
		return enrollmentObject(a)
	}
	if err := validateRecoveryArchive("speaker.recovery_list", aiiosdk.Object(`{"_host_now_ms":1}`)); err != nil {
		t.Fatal(err)
	}
	if err := validateRecoveryArchive("speaker.recovery_delete", confirmed(map[string]any{"archive_sha256": id})); err != nil {
		t.Fatal(err)
	}
	for name, args := range map[string]aiiosdk.Object{
		"a path":           confirmed(map[string]any{"archive_sha256": id, "path": "uid/enrollment.json"}),
		"no archive":       confirmed(nil),
		"a short digest":   confirmed(map[string]any{"archive_sha256": "abc"}),
		"a traversal":      confirmed(map[string]any{"archive_sha256": "../" + id[3:]}),
		"no confirmation":  aiiosdk.Object(`{"archive_sha256":"` + id + `","_host_now_ms":1735787045000}`),
		"a listing filter": aiiosdk.Object(`{"holds":"speaker_registry"}`),
	} {
		op := "speaker.recovery_delete"
		if name == "a listing filter" {
			op = "speaker.recovery_list"
		}
		if validateRecoveryArchive(op, args) == nil {
			t.Fatalf("%s was accepted", name)
		}
	}
	found := map[string]bool{}
	for _, d := range declaredPlugin().Descriptors() {
		if d.ID == "speaker.recovery_list" || d.ID == "speaker.recovery_delete" {
			found[d.ID] = true
			if d.Summary == "" || len(d.Examples) == 0 || d.OperatorConfirms != (d.ID == "speaker.recovery_delete") ||
				d.Input != "schemas/"+strings.Replace(d.ID, ".", "-", 1)+".input.json" {
				t.Fatalf("recovery archive contract incomplete: %+v", d)
			}
		}
	}
	if len(found) != 2 {
		t.Fatal("recovery archive operations absent from discovery")
	}
}
