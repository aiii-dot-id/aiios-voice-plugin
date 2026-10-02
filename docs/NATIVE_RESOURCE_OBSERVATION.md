# Native resource observation contracts

The resource-counter regression must run from a clean source checkout. It no
longer imports an unpublished evidence ZIP or a machine-specific parent path.
`tests/fixtures/native_resource_child.py` preserves the historical diagnostic
child as an AST fixture. It is parsed, not executed as a current speech test.

The restored `windows_voice_cpu` and `windows_speech_resources` readers retain
Windows process handles, sample cumulative CPU counters, process/system memory,
and NVIDIA device-wide counters. They do not change device or process settings.
The historical GPU reader deliberately requires one assigned GTX 1070; this is
a diagnostic precondition, not a portable plugin runtime dependency. These
readers are not included in the plugin runtime.

`python -m scripts.audit_native_session_resources --rows observations.json
--begin 3 --end 5` validates a measured region. Counter regression, PID or
creation-time changes, process exit, nonfinite counters, inconsistent memory,
and insufficient sampling fail rather than becoming zero utilization. Device
utilization and allocated memory do not prove that an inference operator ran
on that device. The auditor does not certify speech or release readiness.

To instrument an existing diagnostic, supply its source and exact hash:

```sh
python -m scripts.stage_native_resource_session \
  --parent diagnostic.py --sha256 "$DIAGNOSTIC_SHA256" \
  --out resource_child.py
```

This generates source only. The supplied diagnostic must match the explicit
child anchors and provide its own SDK/proof dependencies. The output must be
new. No old archive is downloaded or silently substituted. The regression
compares every SDK construction, readiness, conversation, observation, metric
and shutdown call, including its arguments, before and after instrumentation.
Sampling retires before host shutdown, including the failure path. Added idle
windows make this diagnostic unsuitable for promotion timing.

Run the self-contained gate with:

```sh
python -m pytest -q --fail-on-skips tests/test_native_session_resources.py
```

This repairs that gate's missing-source dependency. It does not certify all
historical tests: other archived diagnostic imports and environment dependencies
must be restored or explicitly accounted for separately, never silently skipped
under a whole-suite claim.
