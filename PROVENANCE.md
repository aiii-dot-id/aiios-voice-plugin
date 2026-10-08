# Provenance

This repository is the source authority for the `id.aiii.voice` plugin.
Build products, model weights and qualification evidence are not source and do
not live here. Released packages and runtime companions are named by digest in
the signed catalog and on the matching release.

## What `MANIFEST.sha256` is for, and what it is not

`MANIFEST.sha256` lists every tracked source file with its sha256, itself
excepted. Inside the repository it adds nothing: Git already binds every file
of a commit. It is for a copy of the source WITHOUT its history, such as a
release's source archive, which can be checked against it file by file
(`python3 -m scripts.check_source_manifest` in a checkout; `sha256sum -c
MANIFEST.sha256` on an extracted archive). Because it must be true of every
commit, every commit that changes a file changes it too.

It covers this repository's files and nothing else. What a release is built
from besides them is recorded elsewhere, each in one place:

- the plugin kit: its revision and its archive's sha256, in
  `plugin/sdk-source.json`;
- the speech synthesis engine: its revision in `docs/DEVELOPMENT.md`, and
  every file of it that a build replaces in `runtime/native_pocket/`, each
  with a notice that gives the upstream file's sha256, the built file's and
  what was changed (`engine_overrides/`, `windows_resident/overrides/`,
  `engine_overrides_linux/`; two patches beside them).
  `scripts/prepare_engine_source.py` makes the source each build was given
  from an upstream checkout, by those digests. In each release the statement
  of changes is beside the engine's licence;
- other third-party sources that are built in: each beside the code that uses
  it, with its notice (`runtime/native_echo/NOTICE`,
  `runtime/native_multitalker/NOTICE`, `runtime/native_uid_ecapa/notices/`,
  `runtime/native_pocket/android/overrides/NOTICE`);
- models: by path, sha256, size and address in the package's own declaration;
- every file of every runtime set, and for each whether this release built it
  or took it unchanged from an earlier one: the release's lineage record,
  published with the release.
