# Public source and catalog preparation — 2026-09-18

Prepared for publication; no GitHub push, asset upload, catalog-main promotion
or live-identity installation was performed. The working package remains
`id.aiii.voice` **0.1.0-beta.4**, minimum host **0.1.8**, one signed archive for
macOS arm64, Ubuntu x86-64 and Windows x86-64.

## Cleanup scope

This follows source `a8f3bd03dfbc246747d0cb9554e5c52cc4d09079`.
No native engine, Go carrier, SDK pin, model, session contract or packaged
payload was changed. Historical reports, tests and evidence were retained.
The original delivery checkout and its running eight-hour test remain intact.

- The root now leads to current operator, development and publication guides.
  Stale build-root and native integration wording was corrected. Dated
  checkpoint reports remain dated, not silently relabelled as current.
- Release notes and catalog store details are explicit source files. Package
  facts are still generated from the exact signed archive, not copied from
  those descriptions.
- A pinned, read-only GitHub workflow runs model-free native contracts on three
  desktops and a small artifact-independent package/catalog suite. Its first
  GitHub execution has not happened; this is configuration, not hosted evidence.
- The tracked-source inventory was refreshed. Tracked-file checks found no
  runtime executables/libraries, weights, recordings, credential-key files,
  generated release directories or symlinks. A targeted private-key-header
  scan found no hits. This is scoped hygiene, not a comprehensive secret audit.

## A real catalog-preparation defect

`check_catalog` compared identity, archive size/hash, URL and platforms, but
did not compare the host version window. Its original implementation accepted
a removed minimum, a weakened `0.1.7` minimum and an invented maximum while the
signed package required `0.1.8`. All three reproduced without altering the
package. It now compares presence and exact value for both bounds; regression
cases also cover null, empty, missing and mismatched upper limits.

`scripts/prepare_catalog_update.py` is a local preparation operation only. It
requires a verified base catalog digest and exact signed-package/host-verdict
bindings. It replaces only the voice row, preserves other entries and unknown
fields, adds the compatibility must-understand requirement if needed, and
refuses ambiguous duplicates, stale bases, conflicting descriptive metadata,
changed handoff bytes or an existing output directory. It cannot sign or push.

## Exact prepared catalog

Base catalog commit: `f486646489aecb88d036777c1c2a7bcb2d180a3b`.
Preparation branch `release/voice-beta4`, commit `3c637b8` in the catalog repo.
The memory plugin's entire entry is preserved. The existing index is updated
in place; there is no alternate catalog or second plugin ID.

| Object | SHA-256 |
| --- | --- |
| Signed voice package | `2f74ccfddb247e8085fa711ad3996ad681227c250b7fe4b5057e5b6c026e119f` |
| Prepared index | `e6f2b36cd2885dcd5b13c5d3bf0c4f89374f1fd4f2f319737d68a6277f38575d` |
| Detached catalog signature | `4d42ceb354c77ff0874fc32103c7332014c7351eee2aef0e121c15c6f3e9b1a1` |
| Catalog signature payload | `80b0227b911eb289ff6bca69ad42efdac4f04070dd4d19cae03c2889fcf51f58` |

The existing authorized platform-release key signed the catalog in place;
no private key was copied. Clean host
`c5066fd6596366c84d0046cfdbd9f9ea1ed6c3b5` accepted it and refused the
changed-byte copy specifically because the signature no longer covered the
index. Host executable SHA-256:
`57b86fea7d4fa4e997adf03ca9ca821986a32f7432f6225eb53d83af089d2d3f`.

## Validation and boundaries

- Focused packaging, catalog, schema, notice and source-closeout checks:
  **78 passed**, no failures or skips; `test-results/publication-cleanup-r2.xml`.
- Fresh model-free native build, following the published build commands:
  **31/31 CTest passed** on macOS; retained under
  `.build/public-source-contracts-r1`.
- A clean tracked-file export ran the public Python test selection without
  `.build`, SDK checkout, weights or private evidence. The separate report is
  retained under `test-results`; this verifies source portability, not an
  installed engine.
- The real host catalog acceptance/refusal proof is
  `test-results/catalog-verification-r1/result.json`. Its positive and negative
  process exit codes and outputs are retained independently.
- All twelve frozen release-owned assets still match `SHA256SUMS`. They were
  not rebuilt or replaced by this cleanup.

The current host mode changes have now landed in the staged `c5066fd6` build;
that removes an earlier source dependency but does not run the joint installed
browser proof. At this preparation checkpoint, the eight-hour meeting test
is still active, not passed. Installed browser journeys and fresh hosted-byte
acquisition are still outstanding. They must not be inferred from this report.

## Publication handoff

Use [PUBLISHING.md](PUBLISHING.md) and the signed asset handoff at
`/path/to/work/staging/voice-0.1.0-beta.4-20260918/` on the build server.
Prepare/publish assets before promoting the catalog. Immediately before
promotion, verify the catalog base has not moved; otherwise regenerate and
re-sign while preserving concurrent updates. Public source remains at the
previous release until the operator authorizes publication.
