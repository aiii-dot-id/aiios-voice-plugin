# Voice beta11.4 build recipe

Beta11.4 rebuilds eight Go carriers against public SDK v0.1.15, uses portable
label and vocabulary regular expressions, and retains the replacement-reply
repair for Linux Full and macOS Full. Native model and runtime selections
are unchanged.

## Select the source for the set you build

The base native implementation is the `v0.1.0-beta.11` source. The four schema fixes in this tree
apply to the whole package. Native source selection is:

| Runtime set | Native source |
| --- | --- |
| Linux Full | Base plus `full-cancelled-reply.patch` |
| macOS Full | Base plus `full-cancelled-reply.patch` |
| Linux Small and Small-CPU | Unpatched base |
| macOS Small | Unpatched base |
| Windows Full, Small and Small-CPU | Unpatched base |

For either repaired Full set, make a separate copy of this source and run:

```sh
git apply --check maintenance/full-cancelled-reply.patch
git apply maintenance/full-cancelled-reply.patch
```

Do not apply this patch in the source copy used for any of the other six sets.
Merely having the patch in the repository does not apply it. The patched copy
has changed files and consequently no longer matches the base source manifest;
retain both the base manifest and the patch digest when recording its build.
The patch changes session implementation and its regression test only. It
admits a replacement after cancellation is accepted, serializes synthesis, and
retires a cancelled reply that was replaced before synthesis began. A playback
stop without cancellation still prevents a replacement until compute retires.

Follow `plugin/NATIVE_BUILD.md` and `docs/DEVELOPMENT.md` for each platform's
native dependencies. Maintenance carrier builds use `maintenance/build_carriers.py`
with Go 1.27.0, the exact public SDK v0.1.15 module and no local replacement.
The SDK archive pinned by `plugin/sdk-source.json` independently checks every
module-cache source file. Populate that cache before running the offline builder.
`maintenance/carrier-matrix.json` supplies the retained runtime-profile hash for
each of the eight targets. The builder uses `-trimpath -buildvcs=false`, writes
new outputs only, and records the actual module sum, build metadata and hashes.

```sh
python maintenance/build_carriers.py --go /absolute/path/to/go --output /absolute/new/carriers
```

Each carrier binds its own runtime profile and cannot be substituted between
sets. `lineage.json` and `carrier-matrix.json` name the native archive hashes,
carrier profiles, and build inputs.

## Release assets

Six unchanged runtime archives, models, and corresponding-source archives use
their existing beta11 asset URLs. The two repaired Full native archives use
the beta11.4 asset URLs. The plugin package binds all assets by content hash.
Build outputs are not installable until packaged and signed; users install
through the AII OS catalog.
