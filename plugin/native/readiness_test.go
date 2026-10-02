package main

import (
	"encoding/json"
	"testing"
)

func TestRealReadinessCannotBeSpawnOrPlaceholder(t *testing.T) {
	for _, input := range []string{
		`{}`, `{"readiness":{}}`,
		`{"readiness":{"models_loaded":3,"accelerator":"cuda","probe_ms":0}}`,
		`{"readiness":{"models_loaded":3,"accelerator":"cuda","probe_ms":40001}}`,
		`{"readiness":{"models_loaded":3,"accelerator":"fake","probe_ms":1}}`,
		`{"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":0}}`,
		`{"readiness":{"models_loaded":4,"accelerator":"cpu_vulkan","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu_vulkan","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-vulkan"},"readiness":{"models_loaded":3,"accelerator":"cpu_vulkan","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-metal"},"readiness":{"models_loaded":3,"accelerator":"cpu_metal","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":5,"accelerator":"cpu_metal","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-vulkan"},"readiness":{"models_loaded":5,"accelerator":"cpu_metal","probe_ms":23}}`,
	} {
		if _, err := readinessReport(json.RawMessage(input)); err == nil {
			t.Fatalf("accepted %s", input)
		}
	}
	r, err := readinessReport(json.RawMessage(`{"readiness":{"models_loaded":3,"accelerator":"cuda","probe_ms":23}}`))
	if err != nil || r == nil || r.ProbeMS != 23 || r.ModelsLoaded != 3 {
		t.Fatalf("measured report: %+v %v", r, err)
	}
	r, err = readinessReport(json.RawMessage(`{"readiness":{"models_loaded":3,"accelerator":"directml","probe_ms":23}}`))
	if err != nil || r == nil || r.Accelerator != "directml" {
		t.Fatalf("native Windows readiness: %+v %v", r, err)
	}
	r, err = readinessReport(json.RawMessage(`{"identity":{"backend":"deterministic-test-not-real-model"}}`))
	if err != nil || r != nil {
		t.Fatal("fixture must not advertise model readiness")
	}
	r, err = readinessReport(json.RawMessage(`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":23}}`))
	if err != nil || r == nil || r.Accelerator != "cpu" || r.ModelsLoaded != 4 {
		t.Fatalf("explicit CPU native correctness profile: %+v %v", r, err)
	}
	r, err = readinessReport(json.RawMessage(`{"identity":{"backend":"native-common-vulkan"},"readiness":{"models_loaded":4,"accelerator":"cpu_vulkan","probe_ms":23}}`))
	if err != nil || r == nil || r.Accelerator != "cpu_vulkan" || r.ModelsLoaded != 4 {
		t.Fatalf("explicit native Vulkan TTS and CPU recognition profile: %+v %v", r, err)
	}
	for _, count := range []int{4, 5} {
		input, _ := json.Marshal(map[string]any{"identity": map[string]string{"backend": "native-common-metal"}, "readiness": map[string]any{"models_loaded": count, "accelerator": "cpu_metal", "probe_ms": 23}})
		r, err = readinessReport(input)
		if err != nil || r == nil || r.Accelerator != "cpu_metal" || r.ModelsLoaded != count {
			t.Fatalf("explicit native Metal TTS and CPU acoustic profile: %+v %v", r, err)
		}
	}
}
