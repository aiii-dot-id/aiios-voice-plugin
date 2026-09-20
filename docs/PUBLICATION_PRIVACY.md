# Publication privacy

Source commits and release-tag history were sanitized on September 20, 2026.
Names of operators/test identities, private machine paths and host aliases were
replaced with neutral labels. Commit identities use the project's public GitHub
no-reply address. Private recovery bundles and the old-to-new commit mapping are
retained outside this repository. Functional source, tests and licensing notices
were preserved; the speaker-label fixture remains the same recognition test.

## What this changes

Rewriting Git history changes commit IDs and source archives generated from
release tags. The existing release tags still identify their corresponding
historical source milestones, not the newest development code. Source IDs quoted
in historical evidence refer to the pre-sanitization history. Privacy rewriting
does not rerun a historical qualification or replace its evidence.

The signed plugin and runtime download assets are unchanged. Some existing
native runtime libraries contain build-location strings. Those need a separately
versioned rebuild with compiler source/debug/macro path normalization, new hashes,
appropriate signatures and focused qualification. Do not edit signed downloads
in place, reuse their digest for changed bytes or claim that rewriting Git
removes embedded binary strings.

The next candidate is `0.1.0-beta.5`. Its privacy-clean rebuild is in preparation;
it is not yet a signed or install-qualified release. The native build module and
binary scanner described in [development](DEVELOPMENT.md) cover release-owned
images. Existing beta.4 assets remain immutable. Privacy normalization alone
does not integrate the speaker-aware reference or resolve mixed-speaker
attribution. The next release must include that native repair and its installed
acceptance evidence: a privacy-only beta.5 is not permitted. See the mandatory
[release gates](BETA5_PREPARATION.md).

Already fetched copies, forks and cached old commit views may survive a history
rewrite. Repository owners must handle any required hosting-provider cache
removal separately. An ordinary cleanup commit cannot remove its ancestors.

## Required checks

Before publication, from a clean checkout:

```sh
python -m scripts.check_public_privacy --history
python -m pytest -q --fail-on-skips tests/test_public_privacy.py
```

The check reads committed source and all release-tag history. It rejects known
credential forms, private home/build roots, internal host aliases, non-example
network addresses, private data files and unapproved author/committer email
addresses. It prints locations and categories, not matched secret values.
Run the source inventory and functional contract tests too.

Automated checks do not recognize every personal name or encoded secret. Review
all changed prose, test labels, public release notes and final binary/archive
strings. Preserve public product names and required upstream attribution; do not
strip third-party license text. Loopback addresses and reserved documentation
addresses are legitimate. Use portable placeholder paths and anonymous speaker
labels in fixtures, and store private evidence under external evidence IDs.

Use the GitHub account's verified no-reply address for public commits. Do not
merge old pre-cleanup branches into the rewritten history. Reapply and review
needed changes without importing their old ancestry. Never push a private backup
bundle, audit report or archived ref to the public repository.
