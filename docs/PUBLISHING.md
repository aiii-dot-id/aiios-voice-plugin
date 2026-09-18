# Prepare and publish a desktop voice release

There is one plugin repository, one unified `.aiiospkg`, and one catalog.
Do not create separate plugin IDs per desktop, a second installer or a parallel
catalog. The package's version, host window and platform declarations are
authoritative; catalog metadata must match them exactly.

## Current preparation

The next prepared tag is `v0.1.0-beta.4`, package
`id.aiii.voice-0.1.0-beta.4.aiiospkg`, minimum host `0.1.8`. The signed archive is
9,477,409 bytes, SHA-256:

`2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f`

The frozen handoff contains twelve release-owned assets (297,400,123 bytes),
`SHA256SUMS`, `publication-plan.json`, `catalog-entry.json`, operator setup,
host signature/tamper verification and upstream download evidence. Seventeen
upstream downloads are hash-bound dependencies, not files to duplicate into
Git. The [handoff report](DESKTOP_BETA4_HANDOFF_20260918.md) names the retained
locations. No public upload is implied by preparation, signing or this file.
Operator direction on September 18: publish the Voice Plugin and catalog;
leave SDK and AII OS repository publication to their owner. The earlier proposed
SDK-source supplement is not part of this upload. Publish the original twelve
assets plus their checksums. Developer SDK-source availability is tracked
separately and is not a plugin installation/runtime dependency.
The copy-ready draft is [RELEASE_NOTES_0.1.0-beta.4.md](RELEASE_NOTES_0.1.0-beta.4.md);
its preparation label must not be replaced by a qualification claim until the
listed gates actually finish.

## Source and artifact pipeline

1. Land a clean source commit with tests and its source manifest. Keep model
   weights, build outputs, credentials, recordings and private proof data out
   of Git. Preserve dated provenance rather than rewriting it as current.
2. Build and qualify each native runtime and its exact carrier. Use
   `scripts/stage_qualified_runtime.py`; record explicit stage/carrier hashes
   and accelerator declarations in the input file for
   `scripts/assemble_guided_beta_candidate.py`. Run each module with `--help`
   for its required inputs. Historical `package_*checkpoint.py` scripts produce
   private diagnostic checkpoints, sometimes with `.invalid` URLs: they are
   **not** the public-release entry points.
3. Assemble with an explicit new version and retained artifact root. Keep the
   compiled settings declaration, all referenced schemas, notices, dependency
   URLs/sizes/hashes and host floor bound to the exact payload. Refuse missing
   evidence instead of replacing it with a readiness flag.
4. Sign the final Windows-owned images before runtime packing. Sign the final
   T3 archive using the authorized existing ceremony. Verify both signature
   types at their real platform boundaries. Signing does not certify speech
   quality or installed behavior.
5. Run `scripts/stage_beta1_signed_publication.py` with explicit candidate,
   signed archive, host-verification receipt, generated assets, upstream
   evidence, SDK tool and a fresh output directory. It verifies the exact
   signed bytes and generates the catalog entry from that archive.

Changing package bytes after signing requires a new signature, new hashes and
catalog derivation. Source documentation/tooling changes that do not alter
the payload do not justify silently rebuilding a qualified binary.

## Prepare the catalog, without publishing it

Verify the existing catalog and signature with the release host's
`aii plugin catalog -catalog-dir /path/to/catalog`. Supply a platform key only
if the intended verifier does not use its shipped pinned root. Record the
catalog commit and SHA-256; refuse to replace a concurrently changed index.

```sh
python -m scripts.prepare_catalog_update \
  --catalog /path/to/catalog/aiios-plugins.md \
  --catalog-sha256 EXACT_VERIFIED_CATALOG_SHA256 \
  --handoff /path/to/signed-handoff \
  --details plugin/catalog-details.json \
  --generated YYYY-MM-DDTHH:MM:SSZ \
  --out /path/to/new-catalog-preparation
```

This replaces only AII Voice's entry, preserves other plugins/unknown fields,
enforces package/compatibility equality and requires `must_understand: compat`
for a bounded host window. Descriptive metadata cannot override signed facts.
It emits the index and its exact hash payload, **not a signature**. Sign with
artifact kind `plugin.catalog`, using the catalog's documented existing
platform-release ceremony. Do not copy a previous signature. Verify the
prepared signed index with AII OS and confirm a changed-byte mutant is refused.

Commit index, signature and signature payload together on a preparation branch.
Never merge/push an index referencing absent release assets. If another plugin
lands first, regenerate from the new signed catalog, preserve that update and
re-sign. This is an update in place, not a replacement of catalog history.

## Publication order (requires operator authorization)

For beta.4 the operator explicitly requested Voice Plugin and catalog
publication on September 18. Publish it as a **prerelease**, preserving the
still-open installed/browser and endurance qualifications in its notes.
This publication direction does not turn those checks into passes. Anonymous
asset size/hash verification remains mandatory before catalog promotion.
The full acceptance sequence below remains the target for a qualified release.

1. Bind the clean public source commit and new tag. Upload the exact frozen
   assets plus checksums and release notes to a **draft prerelease**. Do not
   overwrite an existing tag or published asset with changed bytes.
2. Close the recorded installed/browser acceptance gates on the exact host
   and package. The actual eight-hour run is pending until its terminal
   result exists. A source pass is not a substitute for either gate.
3. Publish the prerelease only after that release decision. Independently
   download every release-owned asset anonymously and compare full body size
   and SHA-256 to the handoff. A successful HTTP HEAD or URL listing is not
   byte verification. Retain upstream model verification separately.
4. Exercise empty-cache selected-platform acquisition, interruption/resume,
   installation and the voice journey on macOS, Ubuntu and Windows. Preserve
   existing user identities/profiles. Use isolated identities for destructive
   refusal tests. Do not present a failed journey as successful download proof.
5. Publish the prepared, verified signed catalog only when the release URLs
   are live and the install gate passes. Check its prior commit/hash again
   immediately before the fast-forward. Read back and verify the public index
   and signature, then confirm that a compatible host offers beta.4 and an
   older incompatible host does not.

The release note must distinguish known English/desktop/accuracy limits from
crashes, dropped audio or data loss. T3 signing, Windows code signing, catalog
signing, native model execution, installed browser audio and broad human
quality are separate acceptance statements.
