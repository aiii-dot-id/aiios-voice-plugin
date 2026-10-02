# Automatic component selection

Full and Small are two resource targets of one voice platform. Small is built
from smaller or more efficient components and an appropriate execution plan.
It must retain STT, TTS, speaker UUIDs and labels, history and filtering, VAD,
interruption, recovery, and the same callable contracts. This document records
the implementation direction. The generic selector and authoring changes are
source implementations, not yet a published host capability. Recorded desktop
execution is established below; Full/Small release and installed qualification
remain separate gates.

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
Retain these owners and interfaces.

Use this seam to substitute or quantize expensive model components. Full and
Small do not need separate session engines or public tool APIs. Each released
composition binds its native libraries, models, settings and measured resource
declarations. Exported voices and languages come from that composition's actual
models. The engine reports the opened configuration so the operator can see
which components and execution plan are in use.

Initially keep UID, VAD and endpointing common between Full and Small. Speaker
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

Initial measurements cover host memory, default NVIDIA device 0's available
CUDA memory, and Apple Silicon Metal with unified accounting. Other providers
and placements remain unknown until their collectors and inference paths are
qualified. Hardware facts are preflight evidence, not proof that a particular
model runs on an accelerator.

## CPU, GPU and NPU placement

Choose placement per component within a qualified composition. Small may put
VAD and UID on CPU, accelerate TTS, and use a compact recognition model on a
different device. Use GPU/NPU where measured end-to-end latency, throughput,
memory or energy improves. Provider availability alone is insufficient.
Mobile uses the same native interfaces with platform builds and a measured
mobile composition; desktop execution does not establish phone readiness.

An explicit Nemotron hearing profile accepts `gpu: -1` for the native
diarizer's CPU path, independently of `encoder_cuda` and the separator's
placement. The bound `nemotron.gguf` remains mandatory. An ONNX separator
still requires its bound model, thread count and CPU/CUDA choice. CPU is
not shorthand for removing `asr_execution`, disabling overlap handling or
changing TTS, UID, VAD or endpoint models. A declared GPU remains mandatory;
there is no automatic CPU retry when that GPU fails. This execution choice
is an implementation capability, not a qualification or Small release claim.

The resident ONNX separator accepts complete inputs through 80,003 samples
at 16 kHz. Longer overlapping turns retain the original unresolved records;
the engine does not crop them to fit. Each call is best effort within 5
times its audio duration, clamped to 4-25 s; expiry cancels only that call and
keeps the turn's unresolved records (see the overlap identity gate). Its CUDA arena uses the measured
1.5 GiB bound and one EP stream. That is not a total GPU reservation: the
selected composition must still measure concurrent recognition and synthesis.

The separator exporter lowers all 289 pinned attention contractions to
explicit matrix multiplication and elementwise operations. The global
attention reduction combines group/time axes within each batch, never across
batches; the shared matrix broadcasts over groups without expanding a
sequence-sized contraction buffer. Learned tensors and dynamic input extents
are unchanged. Unknown equations or ranks refuse. The untouched upstream
reference and cancellation/recovery gates still apply to every exported graph.

This export passed the unchanged 28-case Small recorded SDK lifecycle panel
on a 4 GiB CUDA laptop, including complete overlap, quiet speech, unknowns,
interruption with TTS resident, and restart/recovery. No false attribution or
duplicate known-speaker UUID was accepted; the quieter track in one mixture
abstained explicitly. That is safety evidence, not complete identification
coverage. The sampled shared-device peak was 3,489 MiB; it includes other
applications and is not a declared reservation or a guaranteed peak. Source
export and native numerical/cancellation checks passed on macOS CPU, Ubuntu
CUDA and Windows CUDA. These are component checks, not whole-engine Windows
qualification. Installed/fresh-download, browser, physical-audio and mobile
qualification of this composition remain separate unfinished gates.

The equivalent graph also passed the unchanged 28-case whole-engine recorded
panel on macOS (Metal diarizer/TTS, CPU encoder/separator, existing Core ML UID)
and Windows (CPU hearing/separation/UID, Vulkan TTS). All panels retired their
processes cleanly and bound their actual model, runtime and carrier bytes.
Windows additionally passed all 53 native contracts. The isolated Windows
CUDA separator check uses a different ORT runtime; it does not establish a
whole-engine CUDA composition. Those earlier execution results did not qualify
six released Full/Small sets or browser, physical-audio or mobile acceptance.

Keep execution stable during a speech session. Resource pressure can select a
different compatible composition on the next activation. Do not replace a
live recognizer and lose its acoustic or speaker state. A missing required
provider or allocation failure ends the attempted activation honestly; the host
may then select an eligible qualified composition if its acquisition policy
supports that explicit outcome. Signature, corrupt asset, permission and
containment failures are reported rather than interpreted as limited hardware.

## Desktop composition checkpoint, 2026-09-30

All six separately bound Full/Small desktop compositions passed the unchanged
28-input recorded SDK lifecycle panel. The panel covers acquisition, quiet
speech without minting duplicate UUIDs, unknown speakers, complete unequal-gain
overlap transcripts and attribution, interruption, recovery, restart continuity
and process retirement. Quiet abstention is allowed; wrong or unstable UUIDs
are not. These 168 executions do not establish broad speaker accuracy.

Each composition also passed 13 settings/synthesis cases spanning all ten
voices and five finalized VAD turns within continuously open input, without
per-turn Finish. Windows Full and Small repeated these gates on their final
Authenticode-signed carrier/runtime bytes. Native contract suites passed with
51 macOS, 52 Linux and 53 Windows cases, with no platform exclusion. The
configured integrated source scope passed 546 tests; that is not a claim
that every historical artifact-dependent test was executable.

| Composition | Hearing/separation | Synthesis | Recorded SDK gates |
| --- | --- | --- | --- |
| macOS Full | CPU encoder/separator, Metal diarizer, Core ML UID | Metal | Passed |
| macOS Small | INT8 CPU encoder/separator/diarizer, Core ML UID | Metal | Passed |
| Linux Full | FP16 CUDA encoder/separator, Vulkan diarizer, CPU UID | Vulkan | Passed |
| Linux Small | INT8 CPU encoder/separator/diarizer/UID | Vulkan | Passed on Ubuntu 24.04 with a 4 GiB GPU |
| Windows Full | FP32 CPU encoder/separator/UID, Vulkan diarizer | Vulkan | Passed on the GPU-equipped VM |
| Windows Small | INT8 CPU encoder/separator/diarizer/UID | Vulkan | Passed on the GPU-equipped VM |

Separately bound CPU-only Linux and Windows Small alternatives passed the
same recognition/identity, settings and VAD gates. They do not inherit the
GPU composition's latency or playback qualification. On the Ubuntu laptop,
the median synthesis real-time factor was 2.315 CPU versus 0.439 Vulkan;
above 1 means generation is slower than playback. CPU correctness therefore
does not qualify uninterrupted low-latency streaming. Keep GPU Small preferred
when its actual capacity and execution advantage are established.

The host's measurements decide which set a desktop receives. AII OS 0.1.12,
the first host that reads this contract, measures CUDA (NVIDIA device 0) and
Apple unified Metal but not Vulkan, so it refuses every Linux and Windows set
that requires Vulkan and selects the CPU-only Small set even beside an NVIDIA
GPU. On macOS it counts only free pages as available memory. AII OS 0.1.13
measures Vulkan through the probe shipped beside `aii` (`aii-vulkan-probe`; on
Windows `aii.exe` answers the same subcommand), counts reclaimable macOS
memory, and excludes a set whose declared runtime exceeds the operator's
ceilings before applying preference. Before 0.1.14 a runtime declaration
carried only the compressed size, installed bytes and file count, so the
per-file and depth ceilings met an archive only at extraction, after the
download. AII OS 0.1.14
is the first host that reads a runtime's extent, `largest_file_bytes` and
`depth` as the SDK's `runtime-pack` measures them; every earlier host refuses
those members. Each beta.7 runtime declares its extent, so Voice 0.1.0-beta.7
requires 0.1.14 (operator ruling, 2026-10-01, replacing the earlier 0.1.13
requirement). Assembly refuses a lower minimum host, and the SDK refuses a
package at that floor whose runtimes omit the extent.
Unknown Vulkan capacity is never relabeled CUDA or declared as zero memory to
admit a GPU set.

Linux Full's runtime is about 1.59 GB compressed and 2.50 GB installed, and
its largest file (`libcudnn_engines_precompiled.so.9`) is 547,383,096 bytes;
its stage records the exact ceilings it needs (`requires_operator_ceilings`).
The default ceilings (512 MiB compressed, 1 GiB installed, 512 MiB per file)
exclude it, so the host selects Linux Small unless the operator raises all
three. From its declared extent, 0.1.14 names `max_file_bytes` with the other
two before downloading anything; on 0.1.13, raising only the compressed and
installed ceilings lets the host select Full, download it and then refuse it
at extraction.
Member-size and extraction checks remain in force; nothing raises an
operator's ceilings silently. Production selection measured on 2026-10-01 with
the beta.7 package: RTX 4070 Ti Linux, Small-CPU on 0.1.12 and Small on
0.1.13 source; GTX 1070 Windows, Small-CPU on 0.1.12 and Full on 0.1.13 source;
an Apple Silicon Mac, Full on both.

One package still owns each composition and its exact model subset. A published
host of at least the package's stated minimum, current third-party notices,
final package signature, fresh selected-only downloads and installed
containment/recovery checks are separate required release gates. Recorded PCM
and simulated playback receipts are not browser/physical-audio or mobile
acceptance.

## Small implementation order

1. Reduce the largest recognition weights and redundant dependency closure
   first. Preserve explicit weight ownership and independent track and
   recurrent state. The real-model shared encoder probe checks alternating
   contexts against unshared inference, isolated cancellation, reset recovery,
   incompatible-binding refusals and owner retirement. It does not qualify a
   whole Small composition.
   Quantization or a smaller model must pass the same speaker attribution,
   transcription, overlap, opening-word and interruption/recovery gates.
2. Measure the full Small composition on a 4 GB GPU laptop, including CPU/GPU
   coexistence, first transcript and first audio, continuous listening and
   meeting memory bounds. A lighter download alone does not qualify Small.
3. Bind and qualify each Small desktop runtime on Windows, Ubuntu and macOS.
   Prove automatic resource selection and selected-only fresh downloads through
   the host. Then qualify the native composition on physical iOS and Android.

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
(`RuntimeExtentMinHost`; aii-plugin-sdk f8b4961 or later): an earlier SDK
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

The generic selector's signed-package acquisition tests prove the selection
contract; they do not replace voice-model, installed or physical qualification.
No unqualified composition becomes eligible merely because its packaging passed.

When ambiguity or inseparable overlap prevents identification, retain the
utterance with its unresolved status. Reduced resources never justify assigning
another person's UUID or minting unstable duplicates. Resolution coverage and
false attribution are measured separately.

Each composition's recorded, installed, browser and physical results remain
explicit in release evidence. Operator-visible Full/Small is a simple choice
of resource target, while the engine retains the flexibility to evolve the
underlying components.
