# Pinned native path derivation inputs

These three unmodified files come from `0xShug0/audio.cpp` revision
`3174e6b26f11a0e39b4f150961dce98f43ba860d`. Their Apache-2.0 license is included.
Their exact SHA256 values are enforced by `scripts/stage_native_path_shim.py`.
They are test/build inputs, not models or installed runtime payloads.

The derivation changes only seven I/O path lookups and one physical cache key.
The regression reverses those changes and compares every byte against these
inputs; no external checkout or network download is required to run it.

To generate build inputs from an independently obtained upstream checkout:

```
python -m scripts.stage_native_path_shim --upstream /path/to/audio.cpp --output /path/to/new-output
```

The source hashes must match; the destination must not already exist.
