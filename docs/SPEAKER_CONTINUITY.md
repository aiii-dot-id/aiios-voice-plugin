# Speaker continuity and decision evidence

An anonymous UUID is a grouping claim, never authentication. A display label
does not change acoustic evidence or confer permission. Older profiles may
contain one recording; newly corroborated profiles retain two. Both are
immutable on a match. Persistence across sessions does not establish
robustness across microphones, conditions or overlap.

## Joint enrolled and anonymous decision

Each separated track is embedded once and compared against both the enrolled
and anonymous galleries. The stronger accepted match wins only when it is
separated from the competing gallery by the bound margin; close, ambiguous or
near-threshold evidence remains unresolved. An enrolled match emits its stable
`speaker_id` and label. An anonymous match emits its canonical UUID, not an
enrolled identity. Missing enrollment is a valid empty gallery only when the
private store explicitly reports `FS_NOT_FOUND`; unreadable or incompatible
bytes still fail closed. An anonymous profile cannot be admitted if either of
its corroborating samples lies near an existing enrolled or anonymous profile.
Two simultaneous tracks in one utterance cannot both claim the same enrolled
ID or canonical UUID.

The enrolled ID is not silently converted into an anonymous UUID. Existing
anonymous profiles and historical turns are unchanged. This closes the live
track path that previously bypassed enrollment and the enrolled-first choice
that could discard a stronger anonymous match. It does not merge old UUIDs,
guarantee acoustic identity across changed conditions, or establish
authentication.

## Decision diagnostics

An observation with qualified acoustic evidence carries optional `match` data:
outcome, reason, candidate count, nearest candidate UUID, cosine score, runner-up
margin, threshold, minimum margin, compared profile revision, model binding,
policy digest and evidence sample count. Score/candidate are absent for an empty
registry; margin is absent for fewer than two candidates. With no qualified
acoustic evidence, `match` is absent, not a fabricated zero. Candidate UUIDs
inside diagnostics are not accepted identities or filter keys.

These fields survive the resident event and its bounded status reconciliation.
They carry no audio, embedding vectors, transcript, or filesystem paths. The
host consumer must preserve this distinction when displaying decision evidence;
the generic SDK needs no change. Unknown diagnostic fields are refused rather
than copied to consumers. The existing 128-row attribution bound is unchanged.

## Qualification order

1. Preserve private failing profiles outside source control. Reproduce the
   actual match through the production functions. The development-only
   `scripts/audit_speaker_profile_pair.py` emits redacted decisions, not vectors.
2. Evaluate independently labelled recordings across microphones, separate
   sessions, quiet speech, interference and overlapping speech. Measure both
   same-person fragmentation and different-person false matches. Original
   captures and declared train/calibration/test partitions must be retained
   privately with consent; extracted embeddings cannot reconstruct them.
3. Compare evidence selection and profile representations at fixed acceptance
   criteria. Do not lower a threshold just to join a failing pair, infer a
   person's identity from transcripts, or automatically adapt a profile from a
   possibly contaminated match. Synthetic tests establish mechanics only.
4. Keep speech admission explicit in the host: all, only listed UUIDs, or ignore
   listed UUIDs with an explicit unidentified-speaker rule. Meeting mode records
   room speech without answering it. Identification is not proof of addressing
   the assistant, and admission is not authorization for downstream actions.
5. Use restart-unique session references and distinguish microphone/input,
   output playback and terminal/draining states from the existing session truth.

Until the acoustic evaluation passes, a diagnostic improvement is not a claim
that speaker fragmentation or general overlap attribution has been repaired.

## Excluding uncertain audio from speaker evidence

The speaker-conditioned audio selector must distinguish confident inactivity
from uncertain activity on the selected track itself, not just other tracks.
Only confidently inactive frames can bridge pauses. An own-track probability
between the existing inactivity and activity thresholds now ends the candidate
region, just as uncertain competing-speaker activity does. The boundary guards
still apply, and an earlier sufficiently long clean region remains available.

The audio owner gathers guarded clean regions from the same track, in original
sample-clock order, up to ten seconds and 128 regions per track. Uncertain gaps
are omitted rather than embedded or filled with invented silence. Two seconds
of confident active audio are still required in total. The private session seam
validates every region, their non-overlap, and their exact summed PCM length;
the sample count is not a claim that the gathered audio was one contiguous span.
The existing four-track and twelve-second rolling-buffer limits remain in force.
No cross-track pooling or synthetic repetition is used to meet the minimum.

Previously an uncertain region could join two individually insufficient clean
regions into an accepted voiceprint sample. The retained regression fails on
that implementation. This correction changes audio selection, not the UID model,
matching threshold, stored profiles or speaker-identity associations. It does
not demonstrate that a particular real-world duplicate UUID was caused by that
uncertainty, nor guarantee that short or ambiguous speech can be identified.

## Correcting a known duplicate

`speaker.link` is an operator-confirmed correction, not automatic recognition.
After independently establishing that two UUIDs denote the same person, supply
`speaker_uuid`, `target_uuid`, and the exact `registry_revision` from
`speaker.buckets`. It works without an open microphone. The host confirms these
exact arguments using the existing SDK mechanism; no caller-made confirmation
token or new SDK capability is introduced.

Only future **accepted** source matches resolve to the target UUID and label.
The two acoustic profiles remain byte-identical, compete independently, and
retain the original threshold and margin. Even ambiguity between two linked
profiles remains ambiguity. The diagnostic candidate names the actual matched
profile. No averaging, adaptive learning, automatic linking or retrospective
transcript rewriting occurs. Host UUID filters see the chosen target on future
accepted matches; this consequence must be understood before confirming.

The list retains both original UUIDs and exposes `canonical_uuid` and the latest
`link_revision`. Setting target equal to source undoes its correction. Mutation
history is retained within the existing 1,024-entry bound. Chains, cycles,
missing profiles, stale revisions, and forgetting a target still referenced by
another UUID refuse. Broker compare-and-swap, durable receipt and readback are
required before success. Older unlinked documents keep their original bytes.

This provides a reversible repair for confirmed duplicate identities. It does
not prevent the acoustic model from fragmenting a previously unseen recording,
prove that any two people are the same, or qualify broad speaker accuracy.

## Unresolved observations are not new identities

Missing clean evidence and ambiguous matches return uncertain attribution without
a speaker UUID. The existing session/final/track reference identifies the
observation. Diagnostics remain available when comparison was possible. Neither
case publishes a registry document, changes its revision, nor consumes one of its
256 speaker slots. Previously stored provisional rows are preserved, including
their labels and references; confirmed associate/forget still work on them.
There is no automatic deletion, merger or acoustic promotion of those rows.

The host must retain unresolved text under its bounded recent-heard reference,
not manufacture a person UUID or deliver an uncertain final as an approved
operator instruction. Only a qualified new acoustic profile creates a durable
identity. A label by itself never supplies missing acoustic evidence.

The private `speaker.buckets` projection exposes the exact PCM SHA-256
digests already bound to each acoustic profile. This permits a reviewer to
correlate a saved, source-bound recording with the profile that used it before
requesting the existing operator-confirmed, reversible `speaker.link`.
Matching digests prove identical acoustic evidence, not that two *different*
recordings contain the same person. Linking changes future canonical UUID
presentation only; it does not pool examples, relax a match threshold, replay
withheld text or grant authority. Profiles without acoustic evidence have no
digest list.

## Consumer readback

Successful speaker management replies contain `speech`: session reference,
sequence, lifecycle, input state, synthesis state, playback receipt state and
the existing bounded attribution snapshot. It is sampled on the control owner
when the reply is emitted, not when asynchronous storage work was admitted.
Closed input says closed; an output-only session says absent, not listening.
Playback counters are client receipt evidence, not acoustic measurements.
Readback neither opens a microphone nor enrolls anyone. The engine does not
know whether the browser hardware is currently capturing.

## Recent speech versus interactive admission

The operator requires unapproved speakers to remain discoverable in a bounded,
searchable recent transcript buffer without entering interactive conversation.
This is host work: it already owns final text, session references, attribution
and admission. The plugin must not grow a second transcript archive. UUID labels
and external identity links remain presentation; relationships do not alter
acoustic evidence. Explicit retrieval is historical observed speech, not a new
command, and changing a label or approval list never replays withheld words.

The existing discard-on-withhold host behavior does not meet that requirement.
A host implementation must bound age, bytes and entries; preserve exact final
references for late amendments; enforce SAFE; document expiry; and expose typed
list/search/retrieval operations. This is a requirement, not a shipped claim.

## Fixed-policy development comparison

`scripts/audit_uid_cross_utterance.py` recomputes the current native model on
240 public recordings: 30 speakers, one or three reference recordings from one
chapter and five distinct queries from another chapter per speaker. Protocol,
source and audio hashes are frozen before inference. No old model's embeddings
are reused and no threshold is fitted. The correct-reference-removed condition
tests rejection but is not an independently sampled unknown-speaker population.

At threshold 0.56 and margin 0.105, one reference accepted 134/150 correct
queries, rejected 16 and falsely matched none. Three-reference centroids accepted
142/150 and rejected 8 with the correct profile present, but accepted 4/150
incorrectly when the correct profile was removed (versus zero for one reference).
Therefore a blanket switch to three-reference centroids is not accepted as a
continuity fix. These are development component results, not installed,
cross-microphone, overlap or human-level qualification. Neither live profiles
nor the deployed operating point changed.

A subsequent three-reference best-exemplar comparison accepted 141/150 correct
queries and rejected nine, but accepted 3/150 with the correct identity removed
(one-reference baseline: zero). This does not qualify grouped best-exemplar
matching for deployment. Its candidate implementation and failure evidence are
retained privately; production linking still does not relax ambiguity. Confirmed
duplicate competition remains open, separate from the fixed registry-growth bug.

Two independent ONNX embedding models were challenged offline against the same
hash-bound 240-recording, 30-speaker protocol: WeSpeaker ResNet34
(`9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1`)
and ECAPA512-LM
(`d71b85d9b48058ef68004f04f1b78acebefb9dfcf542e19b976a12a5ad1f10b0`).
The official 80-bin fbank frontend and each model's own ONNX graph were used.
These development results applied the *installed model's numeric* threshold
0.56 and margin 0.105 without calibrating either challenger; score scales may
differ, so the numbers are rejection evidence, not a fair release benchmark.
With one reference, ResNet34 accepted 133/150 correct and zero with the correct
speaker removed; ECAPA512-LM accepted 136/150 correct but one with the correct
speaker removed. With three references, ResNet34 accepted 143/150 correct and
three with the correct speaker removed; ECAPA512-LM accepted 142/150 correct and
four with the correct speaker removed. Neither candidate is promoted. This
panel does not measure real mixed speech, changed microphones, or a calibrated
independent-model voting policy.
