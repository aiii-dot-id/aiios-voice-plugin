# Corroborated anonymous profile admission

This correction separates a rejected recognition from a durable new identity.
It does not change the embedding model, recognition threshold, margin, existing
profiles, confirmed links, enrollment, or authorization. The Plugin SDK and
public operation signatures are unchanged.

## Behavior

1. Match the observation against corroborated profiles in the authoritative durable registry.
   Accepted matches return their existing UUID. Ambiguous or missing evidence
   returns no UUID and makes no durable change.
2. An unknown observation returns `speaker_profile_pending` without a UUID.
   Its exact final reference and bounded match diagnostics remain available.
   The engine retains only transient candidate evidence, not a durable bucket.
3. A later unknown observation must match one pending candidate using the
   unchanged recognition threshold and margin. It must come from a different
   utterance and have a different evidence hash. Only then may it create a
   durable profile through the existing CAS and verified-readback path. The
   new profile retains **both** independent recordings, not just the last one.
   If their centroid would match or be ambiguous with an existing profile,
   admission abstains: two individually rejected recordings are not proof of
   a new person. The final utterance's match diagnostics remain its own;
   the centroid veto does not masquerade as that utterance's score.

An observation ambiguous among pending candidates cannot admit a profile, but
is retained as a separate transient candidate. This lets later clean speech
corroborate a clean sample rather than remain stuck between noisy first samples.
The competing candidates are not merged or reinterpreted as one person: the
later observation must win against all remaining candidates under the same
threshold and margin. The ordinary capacity and expiry bounds still apply.

The first observation from the first speaker is subject to the same rule.
There is no empty-registry exception. A label, a transcript, an AI assertion,
a lower threshold or a candidate UUID does not supply corroboration.

## Ownership and bounds

The model owner supplies its process-local monotonically increasing session
epoch; the session supplies its utterance number. Sibling tracks share that
number even though their final-event sequences differ. This travels through
an additive private native callback. Previous native entrypoints and layouts
remain available; missing origin cannot authorize new-profile creation.

The registry-store owner serializes decisions and publication. Pending
proposals are valid only against the exact registry bytes used to stage them.
A different registry snapshot invalidates those proposals; they do not
silently override newer evidence, labels, removals or links.

There are at most 16 transient candidates, each holding one embedding/hash and
origin. Oldest candidates may be evicted. Proposals expire after ten minutes
on a monotonic clock and are pruned on observation, listing or admission access;
there is no background timer or immediate memory-erasure claim. Up to 256
evidence hashes are retained for that same
interval to reject replay, including evidence seen on a sibling track.
When that replay table fills, new-profile admission refuses until entries
expire: it never evicts an unexpired replay fence. Existing-speaker matching
does not depend on available proposal capacity.

No proposal or replay table is persisted. Process restart discards them;
durable UUIDs and profiles survive. The new two-recording profile stores two
bounded model embeddings and evidence hashes, not raw audio. Older durable
profiles are not silently rewritten or migrated.
The same unchanged bounded durable registry and explicit forgetting rules
continue to apply.

## Limits that must not be confused with this correction

### Older single-recording anonymous profiles

Automatic admission requires two different recordings; anonymous recognition
now requires the same. Previously older automatically created singleton rows
were accepted as established competitors under the separate single-recording
**explicit enrollment** policy. These are different evidence paths. A legacy
singleton could veto a valid explicitly enrolled speaker despite not meeting
the anonymous admission requirement itself.

The matching projection excludes those singleton rows. Stored profiles, UUIDs,
labels, relationships, link history and revisions remain byte-identical.
They still participate in duplicate/near-existing admission guards: this is
not permission to mint a replacement UUID. A direct match to only such a row
returns `anonymous_profile_needs_corroboration`, without an accepted UUID or
durable write. No automatic profile adaptation, linking or averaging is added.
Explicit named enrollment still supports one clean recording, unchanged.

`speaker.buckets` distinguishes `profile_available` (stored evidence) from
`matching_ready` (enough anonymous evidence), and supplies `matching_reason`
when false. Historical text is still retrievable by its original UUID. For a
known person with an old singleton, use the existing explicit enrollment flow
with clean evidence; do not fabricate a second sample or delete history.
Existing links do not turn one recording into two or change the acoustic margin.

This intentionally stops singleton anonymous UUIDs from being attributed to
new speech, including UUIDs an operator may previously have approved in a
filter. Their stored history and filter configuration are not rewritten. A
replacement package must make this behavior visible; no live store is migrated.

The copied-gallery regression changes five previously computed clean-microphone
queries from 1/5 to 5/5 correct enrolled decisions, with zero false named or
anonymous accepts on 161 recordings from 25 absent public speakers in both arms,
reproduced by the native decision binaries on macOS and Ubuntu. All
thresholds, embeddings, enrollment bytes and anonymous registry bytes are
identical between arms. This is a small known regression, not an independently
held-out accuracy estimate or installed/browser acceptance. The original
singleton-veto regression fails before the fix. Corroborated close competitors
must still abstain; stored malformed vectors must still refuse before filtering.
Run `scripts/prove_uid_gallery_readiness.py` with explicit native predecessor
and candidate probes and private, frozen input paths to reproduce the decision
gate. Each query is isolated so it cannot enroll a later query.

Two utterances agreeing acoustically is corroboration, not proof that the
person differs from every existing speaker. Consistently degraded audio can
still form a competing profile. The existing matching policy is not newly
calibrated as an open-set novelty classifier by this change.

Different evidence hashes do not prove different underlying source recordings.
The replay fence covers identical selected PCM within its bounded interval,
not re-encoded recordings or adversarially altered audio. Inferred exclusive
activity is not proof of clean separation. These remain acoustic evaluation
requirements; neither a fixture nor this admission rule establishes human-
level identification, spoof resistance or authority to execute commands.

The first pending final is not retroactively relabeled or replayed when a later
utterance creates a UUID. A host must retain withheld speech under its exact
observation reference and must not use `match.candidate_uuid` for approval.
This engine correction does not implement host history or approval filtering.

The two-recording improvement is not a claim of reliable physical-speaker
identity. A speaker can still split under changing microphones, distance,
noise, short utterances or overlap, and two different people can still sound
similar to the encoder. An unresolved observation must remain unresolved;
the plugin never uses a UUID as command authorization.

## Acceptance

Core and broker tests require no publication for first-observation, missing
origin, same-utterance, replay, expired, stale-registry and ambiguous evidence.
Two mutually nonmatching noisy first samples followed by clean speech must
recover only on a later qualifying utterance, never on the ambiguous query or
its sibling track/replay. Distinct competing candidates must remain available.
Distinct corroborating utterances can publish exactly one profile, preserve
diagnostics, survive process restart after publication, and still allow
confirmed naming, linking and forgetting. Native session tests assert that
sibling tracks receive the same private utterance origin.

The source-bound public-speech component audit in `scripts/audit_uid_profile_pooling.py`
compares one- and two-recording profiles on identical queries, with first-half
development and untouched second-half evaluation. At the unchanged policy
threshold 0.56 and a ten-second per-recording cap, held-out known-speaker
matches were 36/40 with one recording and 39/40 with two. The held-out panel
had no wrong known-speaker match and no accepted absent speaker among 25 absent
queries in either arm; it is too small and too clean to establish a low
population false-accept rate. For 50 same-speaker and 250 different-speaker
absent-speaker recording pairs, the new-profile gate admitted 50 and 0
respectively in that panel. Real mixed-speaker, device-shift, browser and
installed-package qualification remain separate release gates.

A second fixed-policy challenge (`scripts/audit_uid_channel_shift.py`) kept
clean references and capped held-out queries at ten seconds, then applied a
deterministic telephone-style bandpass or 20 dB synthetic noise to the queries.
Correct known-speaker matches (one reference → two references) were 36→39/40
clean, 33→36/40 under the bandpass, and 33→39/40 under noise. In each arm and
condition there were zero wrong known-speaker matches and zero accepted absent
speakers among 25 absent queries. These synthetic shifts are diagnostic only;
they do not replace independently labelled physical-microphone, overlap,
reverberation or adversarial tests. The remaining rejects, especially under
the bandpass, are a reason to keep the candidate out of the public release.

Recorded SDK validation retains speech recognition, synthesis, interruption,
recovery, Finish, post-session management and restart checks. Its repeated-
source arrangements are engineering evidence, not independent human recordings
or broad overlap/cross-condition qualification. Deployment and signatures
require newly bound worker and runtime-library bytes.
