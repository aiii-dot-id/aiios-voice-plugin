# Runtime component selection

Full, Small, and CPU-only Small preserve the plugin's callable contracts while
binding different native components, model sets, and resource requirements.

## One engine, selected components

The engine already receives a sealed `native-profile.json` containing model
paths and execution choices. `InstalledProfile` binds those paths beneath the
host's model root. `HearingProfile` selects the recognizer, diarizer, separator,
device and thread count. The factory creates separate live and separated-source
recognizers with one explicitly shared stateless encoder session. Recurrent
caches, cancellation and epochs remain private to each recognizer; decoder and
diarizer contexts are independent. The weight owner ends with that composition,
not through a process-global cache. Competing encoder inference is refused
without a model lock, so cancellation never waits for a sibling's inference.


Use this seam to substitute or quantize expensive model components. Full and
Small do not need separate session engines or public tool APIs. Each released
composition binds its native libraries, models, settings and measured resource
declarations. Exported voices and languages come from that composition's actual
models. The engine reports the opened configuration so the operator can see
which components and execution plan are in use.

UID, VAD and endpointing bindings are explicit in each set. Speaker
embeddings and matching policy determine durable profile compatibility; changing
STT or TTS must not silently replace them. Any later UID change requires an
explicitly tested bridge or preservation of the previous embedding model.
UUIDs and labels are durable registry data, not hashes of today's embedding.

## Selection before download

The host chooses from signed, qualified compositions for the current platform
and architecture. Its hardware facts determine eligibility; the release's
declared preference determines which eligible composition wins. Full is
preferred when eligible. Small is the alternative for limited machines.
The host downloads only the chosen runtime and model closure; shared assets
already present are verified and reused.

Measure complete compositions, not isolated components. STT, separation,
diarization, TTS and other applications can compete for the same device memory.
A composition that fits during recognition alone may fail during interruption
and recovery while synthesis is resident. Test that coexistence and declare
reservations for the complete active engine, including caches and work buffers.
Dedicated GPU memory and Apple unified memory need different accounting.

Selection is a generic plugin-host capability. The public SDK remains a package,
resource and execution contract applicable to other plugins. Voice owns the
model combinations and their evidence; the host owns hardware discovery,
eligibility and acquisition. Resource-aware selection uses a signed
`variant_preference` containing every variant exactly once, plus the existing
per-variant accelerator profile, runtime declaration and model subset. Native
sets declare host and device reservations and any `required_accelerators`.
Unknown capacity cannot satisfy a requirement. Unified memory is charged once
in the host reservation, with zero discrete device reservation.

The implementation checks eligibility before acquisition and repeats
the selected set's capacity check before startup. Only that set is downloaded;
loss of capacity does not silently swap a prepared set. It reuses facility
admission and retry rather than adding a voice planner or scheduler. Omitting
the preference keeps existing packages on their prior selection path. The
minimum host version is 0.1.12; a compatible published host must be available
before a released voice package declares this contract.

Hardware discovery does not establish inference support. A set must name
the accelerator domains its components actually use.

## Compatibility and capacity

Runtime extent fields `largest_file_bytes` and `depth` require AII OS 0.1.14.
Assembly refuses a lower package floor. The host checks download size,
installed size, maximum member size, depth, and memory reservations before
acquisition. Unknown accelerator capacity does not satisfy a GPU requirement.
Linux Full can exceed the default archive and per-file ceilings; users must
explicitly raise the limits shown by the host if they want that set.

Keep a composition stable during an active speech session. A required provider
or allocation failure ends that activation; it does not silently substitute
CPU execution or change recognition models. Ambiguous speech remains unresolved
and never receives another speaker's UUID to improve apparent coverage.

## Packaging a component family

The publisher indexes every composition by `variant_id`, never by operating
system alone. Its explicit input object has `variants` (a map of IDs to bindings)
and `variant_preference` (every ID exactly once, in the release owner's chosen
order). Each binding names `platform`, `arch`, `stage`, `stage_sha256`, `carrier`,
`carrier_sha256` and the complete `accelerator` declaration; a GPU set also names
its `reservation_evidence`. Every desktop must be represented; multiple sets on
the same desktop remain distinct.

Stage each qualified checkpoint with `stage_qualified_runtime --variant-id`
for its declared set. Staging reads that carrier's actual descriptors, and all
sets must expose the same callable contract and settings. An older stage without
this evidence must be re-staged, not edited to look current. Runtime archives,
carriers and selected model inventories stay byte-bound per set. The package
contains the shared model union, while each accelerator profile selects exactly
the models in its qualified stage. Source tests do not qualify those models.

Supply the packaging SDK separately using `--authoring-sdk`,
`--authoring-sdk-archive` and `--authoring-sdk-archive-sha256`, with an explicit
`--go` toolchain. Its entire source tree must match the frozen archive before
and after assembly, and it must read the runtime extent at 0.1.14
(`RuntimeExtentMinHost`; the SDK runtime-extent contract): an earlier SDK
refuses the fields as unknown members. Each runtime declaration carries
`largest_file_bytes` and `depth` from its stage's `runtime_archive`, checked
against that archive's own tree, so the package's minimum host must be at
least 0.1.14. The carrier's existing sealed SDK pin is verified and is
not changed by this authoring choice. The SDK remains the authority for manifest
syntax, host-version floors and resource fields; assembly preserves supplied
host/device reservations, required domains and startup allowances, never
deriving a reservation from a short-run peak.

A device reservation needs independently measured justification. A set whose
`backend` uses Metal, Vulkan or CUDA must list exactly those domains in
`required_accelerators`; an omitted, relabelled or extra domain, or a backend
token the host cannot measure on that desktop, is refused. A discrete Vulkan or
CUDA set declares a positive `device_memory_bytes`: zero would claim no device
allocation and make the host skip its device check. Apple unified Metal declares
`device_memory_bytes=0` and carries its footprint in `memory_bytes`. CPU sets
declare zero device memory and no domain. Each GPU binding's
`reservation_evidence` names a `path` and `sha256` for an
`aiii.voice.accelerator-reservation-evidence.v1` record with `passed` and
`complete_composition` true, the set's `variant_id`, the staged
`runtime_manifest_sha256` and `domains.<name>.measured_peak_bytes`. Each
reservation must cover its measured peak (unified peaks against
`memory_bytes`). The evidence bounds the claim from below; the reservation
remains the release owner's budget.

`variant-plans.json` and both unsigned/signed publication handoffs retain plans
per variant, including its operating system and model subset. Common settings,
schemas and notices may be reused only when their actual bindings agree.
Distribution decisions naming a Windows component must hold on every Windows
set, not merely the first one. A different library or model cannot inherit
another set's review.
