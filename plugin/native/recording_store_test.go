package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/aiii-dot-id/aii-plugin-sdk/pkg/aiiosdk"
)

type fakePrivateStorage struct {
	files map[string]recordingEntry
	paths []string
}

func (f *fakePrivateStorage) HostCallTo(_ context.Context, operation string, target any, _ any) (aiiosdk.Object, error) {
	name, ok := target.(map[string]any)
	if !ok || name["root"] != "private" {
		return nil, errors.New("foreign private root")
	}
	path, ok := name["path"].(string)
	if !ok {
		return nil, errors.New("missing private path")
	}
	f.paths = append(f.paths, operation+":"+path)
	result := map[string]any{"root": "private", "path": path}
	switch operation {
	case "fs.list":
		rows := make([]recordingEntry, 0)
		for file, entry := range f.files {
			if strings.HasPrefix(file, path+"/") {
				rows = append(rows, entry)
			}
		}
		sort.Slice(rows, func(i, j int) bool { return rows[i].Name < rows[j].Name })
		truncated := len(rows) > 1024
		if truncated {
			rows = rows[:1024]
		}
		result["entries"], result["truncated"] = rows, truncated
	case "fs.read":
		row, present := f.files[path]
		if !present || row.Dir || row.Size == nil {
			return aiiosdk.Object(`{"status":"failed","reasonCode":"FS_NOT_FOUND"}`), nil
		}
		if row.Symlink {
			return aiiosdk.Object(`{"status":"denied","reasonCode":"FS_SYMLINK_REFUSED"}`), nil
		}
		result["size"], result["offset"], result["bytes"], result["data_b64"] = *row.Size, 0, 0, ""
		if *row.Size > 0 {
			result["bytes"], result["data_b64"] = 1, "AA=="
		}
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

func TestPrivateRecordingRetentionEndToEnd(t *testing.T) {
	now := time.Now().UTC()
	id := strings.Repeat("a", 64)
	old := now.Add(-time.Hour).Format(time.RFC3339)
	recent := now.Format(time.RFC3339)
	size := int64(320044)
	f := &fakePrivateStorage{files: map[string]recordingEntry{
		"recordings/" + id + ".wav":        {Name: id + ".wav", Size: &size, Modified: old},
		"recordings/." + id + ".pending":   {Name: "." + id + ".pending", Size: &size, Modified: old},
		"uid/.speakers-" + id + ".pending": {Name: ".speakers-" + id + ".pending", Size: &size, Modified: old},
		"uid/.captures-" + id + ".pending": {Name: ".captures-" + id + ".pending", Size: &size, Modified: recent},
		"uid/enrollment.json":              {Name: "enrollment.json", Size: &size, Modified: old},
	}}
	c := newCarrier(nil, nil, func() {})
	defer c.cancel()
	list, err := c.recordingStoreCall(context.Background(), f, "recording.list", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(list)
	if !strings.Contains(string(raw), id) || strings.Contains(string(raw), "pending") {
		t.Fatalf("saved inventory differs: %s", raw)
	}
	pruned, err := c.recordingStoreCall(context.Background(), f, "recording.prune", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = json.Marshal(pruned)
	if !strings.Contains(string(raw), `"deleted":2`) {
		t.Fatalf("stage prune receipt differs: %s", raw)
	}
	if _, ok := f.files["recordings/"+id+".wav"]; !ok {
		t.Fatal("prune deleted saved WAV")
	}
	if _, ok := f.files["uid/enrollment.json"]; !ok {
		t.Fatal("prune deleted live UID store")
	}
	if _, ok := f.files["uid/.captures-"+id+".pending"]; !ok {
		t.Fatal("prune deleted recent upload")
	}
	deleted, err := c.recordingStoreCall(context.Background(), f, "recording.delete", aiiosdk.Object(`{"recording_id":"`+id+`"}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = json.Marshal(deleted)
	if !strings.Contains(string(raw), `"deleted":true`) {
		t.Fatalf("saved WAV delete receipt differs: %s", raw)
	}
	if _, ok := f.files["recordings/"+id+".wav"]; ok {
		t.Fatal("saved WAV retained")
	}
	for _, path := range f.paths {
		if strings.Contains(path, "enrollment.json") || strings.Contains(path, ".captures-") {
			t.Fatalf("host was asked to delete protected data: %s", path)
		}
	}
}

func TestRecordingBrokerReceiptsFailClosed(t *testing.T) {
	for _, raw := range []string{
		`{"status":"succeeded","operation_result":{"root":"private","path":"other","entries":[]}}`,
		`{"status":"succeeded","success":false,"operation_result":{"root":"private","path":"recordings","entries":[]}}`,
		`{"status":"succeeded","ok":false,"operation_result":{"root":"private","path":"recordings","entries":[]}}`,
		`{"status":"failed","reasonCode":"FS_NOT_FOUND","reason_code":"FS_IO_FAILED"}`,
		`{"status":"denied","reasonCode":"FS_NOT_FOUND"}`,
	} {
		if _, _, err := hostRecordingResult(aiiosdk.Object(raw), "recordings"); err == nil {
			t.Fatalf("broker contradiction admitted: %s", raw)
		}
	}
	if _, absent, err := hostRecordingResult(aiiosdk.Object(`{"status":"failed","reasonCode":"FS_NOT_FOUND"}`), "recordings"); err != nil || absent != "FS_NOT_FOUND" {
		t.Fatalf("missing directory not distinguished: %v %q", err, absent)
	}
	value, absent, err := hostRecordingResult(aiiosdk.Object(`{"status":"succeeded","operation_result":{"root":"private","path":"recordings","entries":[],"truncated":false}}`), "recordings")
	if err != nil || absent != "" {
		t.Fatalf("valid inventory rejected: %v %q", err, absent)
	}
	if rows, truncated, err := recordingInventory(value); err != nil || truncated || len(rows) != 0 {
		t.Fatalf("empty inventory rejected: %v", err)
	}
}

func TestStagePruneSelectsOnlyExpiredExactPrivateStages(t *testing.T) {
	now := time.Date(2026, 9, 23, 12, 0, 0, 0, time.UTC)
	id := strings.Repeat("a", 64)
	size := int64(1200)
	old := recordingEntry{Name: "." + id + ".pending", Size: &size, Modified: now.Add(-3 * time.Minute).Format(time.RFC3339)}
	if !expiredVoiceStage("recordings", old, now) {
		t.Fatal("expired recording stage not eligible")
	}
	for _, row := range []recordingEntry{
		{Name: old.Name, Size: &size, Modified: now.Add(-time.Minute).Format(time.RFC3339)},
		{Name: old.Name, Size: &size, Symlink: true, Modified: old.Modified},
		{Name: id + ".wav", Size: &size, Modified: old.Modified},
		{Name: "." + id + ".pending.extra", Size: &size, Modified: old.Modified},
		{Name: "." + strings.ToUpper(id) + ".pending", Size: &size, Modified: old.Modified},
	} {
		if expiredVoiceStage("recordings", row, now) {
			t.Fatalf("unsafe recording stage selected: %+v", row)
		}
	}
	for _, prefix := range []string{".speakers-", ".enrollment-", ".captures-", ".recovery-"} {
		row := old
		row.Name = prefix + id + ".pending"
		if !expiredVoiceStage("uid", row, now) {
			t.Fatalf("expired UID stage not eligible: %s", prefix)
		}
	}
	old.Name = ".something-" + id + ".pending"
	if expiredVoiceStage("uid", old, now) {
		t.Fatal("foreign private file selected")
	}
}

func TestRecordingInventoryRefusesIncompleteListing(t *testing.T) {
	for _, raw := range []string{
		`{}`, `{"entries":null}`,
		`{"entries":"not-an-array"}`,
	} {
		if _, _, err := recordingInventory(json.RawMessage(raw)); err == nil {
			t.Fatalf("incomplete inventory accepted: %s", raw)
		}
	}
	id := strings.Repeat("a", 64)
	rows, _, err := recordingInventory(json.RawMessage(`{"entries":[{"name":"` + id + `.wav","dir":false,"size":320044}]}`))
	if err != nil || len(rows) != 1 || rows[0].Size == nil || *rows[0].Size != 320044 {
		t.Fatalf("valid recording not listed: %v", err)
	}
}

func TestRecordingRecoveryBeyondHostListingLimit(t *testing.T) {
	f := &fakePrivateStorage{files: map[string]recordingEntry{}}
	size := int64(1 << 20)
	for i := 0; i < 1025; i++ {
		id := fmt.Sprintf("%064x", i)
		f.files["recordings/"+id+".wav"] = recordingEntry{Name: id + ".wav", Size: &size}
	}
	c := newCarrier(nil, nil, func() {})
	defer c.cancel()
	list, err := c.recordingStoreCall(context.Background(), f, "recording.list", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(list)
	for _, descriptor := range declaredPlugin().Descriptors() {
		if descriptor.ID == "recording.list" && len(raw) > descriptor.MaxResultBytes {
			t.Fatalf("full inventory exceeds declared tool result limit: %d > %d", len(raw), descriptor.MaxResultBytes)
		}
	}
	if !strings.Contains(string(raw), `"truncated":true`) {
		t.Fatalf("partial inventory concealed: %s", raw)
	}
	f.paths = nil
	// This exact ID is beyond the first page, not just present in a partial list.
	id := fmt.Sprintf("%064x", 1024)
	_, err = c.recordingStoreCall(context.Background(), f, "recording.delete", aiiosdk.Object(`{"recording_id":"`+id+`"}`))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := f.files["recordings/"+id+".wav"]; ok {
		t.Fatal("overfull directory trapped recording")
	}
	for _, call := range f.paths {
		if strings.HasPrefix(call, "fs.list:") {
			t.Fatal("exact deletion still requires inventory")
		}
	}
	list, err = c.recordingStoreCall(context.Background(), f, "recording.list", aiiosdk.Object(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = json.Marshal(list)
	if !strings.Contains(string(raw), `"truncated":false`) {
		t.Fatal("inventory did not recover")
	}
	// Missing, directories and symlinks are not mistaken for disposable files.
	for _, row := range []recordingEntry{{Dir: true, Size: &size}, {Symlink: true, Size: &size}} {
		f.files["recordings/"+id+".wav"] = row
		f.paths = nil
		_, _ = c.recordingStoreCall(context.Background(), f, "recording.delete", aiiosdk.Object(`{"recording_id":"`+id+`"}`))
		for _, call := range f.paths {
			if strings.HasPrefix(call, "fs.delete:") {
				t.Fatal("non-regular target deleted")
			}
		}
	}
}
