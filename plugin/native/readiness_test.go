package main

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

func TestRealReadinessCannotBeSpawnOrPlaceholder(t *testing.T) {
	warm := defaultLimits.WarmProbe
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
		// The Python engines' reports: three models under an accelerator and no backend.
		`{"readiness":{"models_loaded":3,"accelerator":"cuda","probe_ms":23}}`,
		`{"readiness":{"models_loaded":3,"accelerator":"directml","probe_ms":23}}`,
		`{"readiness":{"models_loaded":3,"accelerator":"metal","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":3,"accelerator":"cpu","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":6,"accelerator":"cpu","probe_ms":23}}`,
		// A recognizer handed in from outside names no measured placement.
		`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"external_recognizer","probe_ms":23}}`,
		`{"identity":{"backend":"native-common-metal"},"readiness":{"models_loaded":5,"accelerator":"external_recognizer","probe_ms":23}}`,
	} {
		if _, err := readinessReport(json.RawMessage(input), warm); err == nil {
			t.Fatalf("accepted %s", input)
		}
	}
	r, err := readinessReport(json.RawMessage(`{"identity":{"backend":"deterministic-test-not-real-model"}}`), warm)
	if err != nil || r != nil {
		t.Fatal("fixture must not advertise model readiness")
	}
	r, err = readinessReport(json.RawMessage(`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":23}}`), warm)
	if err != nil || r == nil || r.Accelerator != "cpu" || r.ModelsLoaded != 4 || r.ProbeMS != 23 {
		t.Fatalf("explicit CPU native correctness profile: %+v %v", r, err)
	}
	r, err = readinessReport(json.RawMessage(`{"identity":{"backend":"native-common-vulkan"},"readiness":{"models_loaded":4,"accelerator":"cpu_vulkan","probe_ms":23}}`), warm)
	if err != nil || r == nil || r.Accelerator != "cpu_vulkan" || r.ModelsLoaded != 4 {
		t.Fatalf("explicit native Vulkan TTS and CPU recognition profile: %+v %v", r, err)
	}
	for _, count := range []int{4, 5} {
		input, _ := json.Marshal(map[string]any{"identity": map[string]string{"backend": "native-common-metal"}, "readiness": map[string]any{"models_loaded": count, "accelerator": "cpu_metal", "probe_ms": 23}})
		r, err = readinessReport(input, warm)
		if err != nil || r == nil || r.Accelerator != "cpu_metal" || r.ModelsLoaded != count {
			t.Fatalf("explicit native Metal TTS and CPU acoustic profile: %+v %v", r, err)
		}
	}
}

// A WARM INFERENCE IS GIVEN THE TABLE'S TIME, AND A REPORT OF A LONGER ONE
// SAYS SO. It was 40 seconds typed here and in the worker's library; both
// wait by the table's warm_probe_ms. Stated here as 900 ms: a warm inference
// of 900 is taken, and one of 901 is refused with both numbers and the
// member, whatever 40 seconds would have said.
func TestAWarmInferenceIsGivenTheTablesTime(t *testing.T) {
	if defaultLimits.WarmProbe != 40*time.Second {
		t.Fatalf("the default for a warm inference is %v; it is the 40 s that was typed", defaultLimits.WarmProbe)
	}
	report := func(probe int) json.RawMessage {
		return json.RawMessage(fmt.Sprintf(`{"identity":{"backend":"native-common-cpu"},"readiness":{"models_loaded":4,"accelerator":"cpu","probe_ms":%d}}`, probe))
	}
	const stated = 900 * time.Millisecond
	if r, err := readinessReport(report(900), stated); err != nil || r == nil || r.ProbeMS != 900 {
		t.Fatalf("a warm inference that took the table's 900 ms: %+v %v", r, err)
	}
	const late = "invalid worker warm-inference readiness: the warm inference took 901 ms, more than the 900 ms the limits table gives it (warm_probe_ms)"
	if r, err := readinessReport(report(901), stated); r != nil || err == nil || err.Error() != late {
		t.Fatalf("a warm inference one millisecond past the table's 900: %+v %v", r, err)
	}
	// A table that gives it longer takes what 40 seconds refused.
	if r, err := readinessReport(report(40001), 60*time.Second); err != nil || r == nil {
		t.Fatalf("a warm inference of 40001 ms under a table that gives it 60 s: %+v %v", r, err)
	}
	// And the wait for readiness takes the member from the carrier's table.
	c, _, _ := privateFixture(t)
	c.limits.WarmProbe, c.limits.Ready, c.began = stated, time.Minute, time.Now()
	c.ready <- workerMessage{Ready: report(901)}
	if r, err := c.awaitReady(); r != nil || err == nil || err.Error() != late {
		t.Fatalf("the carrier's wait for readiness did not hold the report to its table: %+v %v", r, err)
	}
}
