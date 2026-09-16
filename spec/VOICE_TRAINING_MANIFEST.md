# AII Voice owned-training manifest

Status: materialized runnable-run contract.

This contract distinguishes a research intention from a runnable owned-model
training claim. The executable authority is
`scripts/validate_voice_training_manifest.py`.

A runnable manifest binds:

- one question, hypothesis, falsifier, and claim limit;
- exact project source files, all resolved and hashed beneath one declared root;
- source-data, training-example, split-authority, and sample-order hashes;
- an assertion that sealed test data was not accessed;
- every teacher model, optional adapter, input set, output cache, and recorded
  failure;
- architecture, optimizer, precision, device class, and environment lock;
- seeds, sample order, exact checkpoint/resume behavior, optimizer updates, and
  a measured or estimated compute budget bound to its accountant;
- the owned artifact license and artifact schema;
- expected output paths.

All four preflight statements—code frozen, data frozen, family-disjoint labels,
and stable labels—must be true. A blocked experiment is documented as a
preflight/stop record and is not mislabeled as a runnable manifest.

Teacher disagreement and failures remain in their individual output caches.
An aggregate teacher target cannot erase them before student training.

Shape validation establishes only internal declaration consistency. The CLI
requires `--materialized-root` and verifies every source, data, teacher,
configuration, environment, sample-order, and compute-accountant file against
its SHA-256 before describing the run as runnable. A hash-shaped string naming
an absent object is not a runnable experiment.

Materialized validation does not establish that the run occurred, reproduced,
improved quality, or produced a valid artifact. Outputs must separately bind
back to this manifest.
