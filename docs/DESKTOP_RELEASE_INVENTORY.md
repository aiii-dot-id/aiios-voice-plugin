# Desktop release inventory

Package assembly must consume the model inventory attached to each successful,
source-bound runtime staging receipt. A historical model count is not a
contract. Each selected download must match the tested path, size and SHA-256;
duplicate, missing, extra and changed selections are refused. A shared package
may contain a union of models, while each platform selects only its own set.

The separated hearing replacement contains thirteen download objects, including
partitioned ONNX graphs, their external data, vocabulary and frontend
coefficients. It replaces only the `stt/` inventory. TTS, VAD, endpointing and
UID bytes cannot change through the hearing-replacement option. Changing those
components requires their own explicit inputs and execution evidence.

The model notice inventory changes atomically with the hearing inventory. It
includes the pinned original model cards, original NVIDIA Open Model License,
attribution, transformation description and every exported model hash. The
source's Apache-2.0 license does not replace model terms. A prior recognizer's
distribution review is not automatically carried over to different weights.

## Execution placement is part of the candidate

The original Windows recognizer declared DirectML execution. The current
multitalker implementation uses ONNX Runtime CPU execution and refuses that
old alternate-execution declaration. The rebuilder therefore requires an
explicit `--hearing-execution cpu` when replacing such a parent. This removes
only the old recognizer's placement declaration; it does not remove Vulkan TTS
or claim that CPU is the optimal platform choice. All changed bytes require
new qualification. A package's vendor notices no longer describe historical
DirectML measurements as measurements of the new recognizer.

Parent models may be relocated with `--parent-models-root`. Every original
size and hash is still verified; missing files, symlinks, directory escapes
and byte changes are refused. Original checkpoints are not modified.

## Ubuntu build floor

The native candidate is built on Ubuntu 24.04 rather than assuming a newer
distribution's binary is compatible. Vulkan compilation requires both `glslc`
and `spirv-headers` in addition to the Vulkan development headers. These are
build dependencies, not permission to install or replace a user's GPU driver.
The resident TTS build selects upstream's `custom` model composite with
`pocket_tts` only, avoiding unrelated model implementations. Release-owned
binaries are scanned after linking for private build paths.

## Qualification remains separate

Model-free contracts, recorded-speech SDK execution, installed host/browser
behavior, publisher signatures, hosted downloads and catalog installation are
different gates. In particular, the host must consume persistent speaker UUIDs
and enforce include/exclude policy on that exact attribution before the UUID
feature can be called installed-qualified. No package helper creates that
claim, upgrades an identity, publishes a release or updates a catalog.
