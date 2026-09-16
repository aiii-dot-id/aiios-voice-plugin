# AII Voice complete-system artifact contract

The complete runtime binds one immutable `system.json`. This is the identity a
Voice Core trace records. A single model manifest is insufficient because the
selected architecture may be a specialist suite, shared trunk, hybrid, or
unified model.

The executable authority is `scripts/validate_voice_system_artifact.py`.

The system manifest binds:

- the current standalone Voice Core protocol;
- the exact Apache-2.0 owned component artifacts and their roles;
- the exact audio frontend, tokenizer/processor, calibration, thresholds, and
  runtime-settings resources used by the system;
- the runtime name, revision, backend, precision, and settings SHA-256;
- the capabilities and physical targets claimed by the aggregate;
- full SHA-256 and byte-size identity for every non-model resource.

Every declared system capability must be supplied by a component. Every
component must declare every target named by the system. The runtime settings
must bind exactly one declared `runtime_settings` resource. Component artifact
validation recursively verifies each model package and all of its payloads.

Passing is structural identity evidence only. It does not establish numerical
parity, quality, performance, physical-platform support, or human-level
behavior.
