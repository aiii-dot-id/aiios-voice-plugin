# Native asset support dependencies

No build-time download or system Python. These sources are unchanged and
retain their MIT notices; the ASR build verifies their hashes.

- PicoSHA2 `161cb3fc4170fa7a3eca9e582cebd27cc4d1fe29`,
  https://github.com/okdshin/PicoSHA2. Header SHA-256
  `b13c180161ffac8d0adc81e033e493c409457c4d1258ab9781ac80579ba3bdd8`.
  Portable SHA fallback and an independently callable cross-check. Apple and
  Windows use their native streaming SHA-256 APIs for large model verification.
- cJSON copied byte-for-byte from the already-bound audio.cpp
  `3174e6b26f11a0e39b4f150961dce98f43ba860d` dependency. Source/header SHA-256
  `607e756460fa0de37d20a7a9181f2de29c97bfb7ce5a0e6c2f548243836cd852` /
  `25b0145150d500498e4d209cec69c18c42cf818bffcc54690be3b895a2a16dee`.
