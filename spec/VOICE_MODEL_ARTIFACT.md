# AII Voice owned-model artifact contract

Status: first executable contract, schema version 1.

An artifact is a self-contained, content-addressed release candidate for one
or more Voice Core capabilities. The executable authority is
`scripts/validate_voice_artifact.py`.

## Required manifest

The artifact root contains `artifact.json` and exactly the files declared by
that manifest. Every declared path, byte length, and SHA-256 is verified. No
symlink, path escape, lock, partial, or incomplete marker is admissible.

Required top-level fields:

- `schema`: `aiii.voice.model-artifact`;
- `schema_version`: `1`;
- `artifact_id`: stable non-empty identity;
- `license`: SPDX expression for the released checkpoint;
- `capabilities`: one or more registered Voice Core capabilities;
- `protocol`: Voice Core schema name and version;
- `lineage`: bound training and source-data manifest SHA-256 values, plus
  teacher observations when applicable;
- `streaming`: statefulness, initialization/reset/terminal semantics, and
  declared lookahead;
- `variant`: precision plus explicit quantization, parent, and conversion
  identity. An unquantized source cannot claim conversion fields; a quantized
  descendant binds its parent and conversion configuration;
- `targets`: platform target ids for which this exact variant is intended;
- `files`: exact path, role, format, bytes, and SHA-256 for every payload.

An artifact may be intended for a target before qualification, but intention
is not support. Platform qualification evidence remains outside the artifact
so rerunning a benchmark cannot mutate the model identity.

## Lineage

Teacher records name immutable model and adapter revisions and their exact
output-cache manifest. Teachers are observations, not ownership authorities.
Conflicting teachers remain distinct. A published project-owned checkpoint is
Apache-2.0, while every source and teacher boundary remains documented in the
training record. The validator enforces Apache-2.0 and registered physical
target ids. Teacher and reference artifacts with other terms remain evidence
sources, not owned release artifacts.

## Streaming law

State initialization, state carry, reset, cancellation, and terminal behavior
are part of the artifact. A runtime cannot substitute its own defaults and
still claim exact artifact execution. Lookahead is declared in source samples;
preprocessing may not hide future context.

## Verification

```sh
python scripts/validate_voice_artifact.py /path/to/artifact
```

Passing means the package is structurally exact and internally bound. It does
not establish numerical parity, quality, latency, or platform support.

Complete modular or hybrid systems are bound separately by
`spec/VOICE_SYSTEM_ARTIFACT.md`; Voice Core traces name that aggregate manifest,
not one arbitrarily selected component model.
