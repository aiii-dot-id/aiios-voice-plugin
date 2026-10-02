# Speaker identity acceptance

Identity correctness and identification coverage are different requirements.
Unresolved quiet speech is an abstention, not a wrong identity. It must retain
its utterance reference and follow the host's withholding/history policy; it
must not manufacture a durable speaker UUID or inherit an existing one.

## Recorded acceptance

Establish two speakers from distinct clean recordings, then exercise normal
speech, repeated quieter recordings, overlap, an additional unknown speaker,
clear recovery and process restart. Capture registry revisions and UUID sets
after each utterance, not only the final speaker list.

- Once acquired, a speaker may match that UUID or remain unresolved. A second
  durable UUID for that same person fails continuity even if later labels agree.
- A different person must not inherit either established UUID. If the unknown
  person acquires a new UUID, its subsequent observations must preserve it or
  abstain, rather than repeatedly create people.
- For overlap, establish which words belong to which reference speaker before
  comparing reported UUIDs. Set equality of the expected and reported UUIDs
  does not suffice: both may be present but swapped.
- Quiet abstention passes the identity-correctness check only if no incorrect
  attribution or duplicate registry admission occurred. Report coverage
  separately. A system that abstains on everything does not pass usability.
- Normal, adequately audible recovery must demonstrate reuse of the acquired
  UUID after uncertain speech and restart. Report initial acquisition time,
  clean/quiet/overlap coverage, errors and latency separately.

`scripts.score_identity_attribution.evaluate` joins every observation to its
exact session, transcript sequence and track. Independent reference text selects
the track permutation without consulting UUIDs. Tied assignments and missing or
extra tracks cannot establish attribution. Excessive word errors on a named
track cannot establish attribution; unresolved text is scored separately. Named
tracks containing another reference speaker's exclusive two-word phrases also
fail. This finite-panel leakage check is not a proof of universal word alignment
or protection from undetected overlap. Single-word substitutions, shared phrases
and naturally ambiguous speech still need audio review and broader evaluation.

The scorer exposes abstention and resolved counts separately. It does not
enforce a universal coverage target or decide that one difficult sample blocks
release. The existing recorded text limits (25% case and 35% per-speaker word
error) are engineering regression limits, not a claim of human-level quality.
The actual recorded protocol must bind its audio, independent reference text,
model, runtime, scorer and thresholds before acceptance; never derive expected
words from the recognizer being tested.

## Release boundary

Do not overwrite earlier reports that required complete identification. Keep
their failed measurements and explain the criterion change. Do not lower model
thresholds or margins merely to improve coverage. Zero observed errors in a
small, reused corpus does not establish a population false-accept rate.

Recorded SDK correctness, installed containment, browser delivery, physical
audio, microphone changes and broad speaker accuracy remain distinct evidence.
All supported release platforms must qualify the exact package bytes. Voice
matching itself is not authorization for sensitive actions.
