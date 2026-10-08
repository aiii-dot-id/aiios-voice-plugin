# Recognizer corrections

The recognizer writes the likeliest words for what it hears. A name it does not
know comes out as a word it does: "Kwin" for "Quinn". A correction list says what
such words meant. It is taught, never trained: no model changes.

## What a rule is

`heard` is one to four words as the recognizer writes them; `meant` is what
replaces them. At most 64 rules, 64 bytes each side.

- A rule matches whole words, without regard to case, joined by spaces only. It
  never matches inside a longer word and never across punctuation.
- A possessive on the last word is kept: "Kwin's" becomes "Quinn's".
- One pass: at each word the rule with the most words wins, and what a rule
  wrote is not read again. Rules do not chain.
- One rule per heard phrase. Teaching a phrase again replaces what it meant.
- Every byte outside a match is left exactly as recognized.

A rule is blind to context. It rewrites its words wherever they stand, so it is
for mis-hearings that are never the right words where the engine listens. A
rule from "queen" to "Quinn" would rewrite every queen. Words that sound alike and
are both real belong to decoding, not to this list.

## What was meant is also preferred

A rule fixes a word after it was written. The recognizer is also told what
every rule meant, and prefers to write that itself where the sound is close:
each `meant` that is one to four words of letters, digits and apostrophes, and
that the model's own word pieces can spell, is a preferred term for the
session. So teaching "Kwin" → "Quinn" both rewrites "Kwin" and makes the
recognizer more likely to write "Quinn" in the first place, including where it
would have written something no rule names.

How a term is preferred (`runtime/native_multitalker/decoder.h`): the decoder
writes exactly what it always wrote. Where it begins a word and the term's
first piece scores close to its choice, a second path weighs the term over the
same frames. When the decoder's word ends, the term takes its place if the
second path spelled the term out within its margin and budget; otherwise the
decoder's word stands. Nothing outside that word can change.

Consequences to know:

- A preferred name is written as the name wherever the decoder writes that
  word: with "Rowan" preferred, "a rowan tree" comes out "a Rowan tree".
- Words that sound the same go to the preferred one: "Rowen" is written "Rowan"
  more often than not.
- It cannot give back a word the recognizer merged into another, split in two
  or did not write at all.
- The session's readback says how many terms the recognizer took:
  `{"revision", "rules", "preferred"}`.
- The margin and budget are provisional constants, chosen on synthesized
  speech; they are not operator settings.

## Where it applies

The list travels beside a session's settings and is pinned with them, like the
voice and the pause. It applies to that session's `transcript_partial` and
`transcript_final` events, in the worker, as the last step before an event
leaves the engine. Speaker identification, enrollment evidence and saved
recordings use audio and are not touched.

A transcript a rule changed carries three things:

| Member | Meaning |
| --- | --- |
| `text` | the corrected words |
| `recognized_text` | the words exactly as recognized |
| `corrections` | how many corrections were made |

A transcript no rule changed has `text` alone, as before. `text` and
`recognized_text` together stay within the bound `text` alone has; a correction
that would exceed it is not made.

Recognized speech is recorded as the speaker's own words. A rule changes that
record, which is why `recognized_text` always travels with a corrected
transcript: whoever receives it can show or keep what was actually heard.

## The stored form

    {"schema":"aiii.voice.corrections","revision":N,
     "rules":[{"heard":"Kwin","meant":"Quinn"}, ...]}

Exactly these members. The engine holds a list whole or not at all: a document
with another shape, a rule outside the bounds above or a phrase listed twice is
refused. A refused list does not take speech away. The session opens and runs
uncorrected, its readback says `{"unreadable": reason}` in place of
`{"revision", "rules"}`, and the worker logs one `AII_VOICE_CORRECTIONS` line
naming the fault and no rule's words.

A session given no list behaves, and reads back, exactly as it did before this
existed.

## Teaching it

Three operations, in the pattern of the speaker operations. An identity can
call them; the two that change the list only propose, and the operator confirms.

| Operation | Does |
| --- | --- |
| `vocabulary.list {}` | The rules, the list's `revision` and its limits. |
| `vocabulary.correct {heard, meant, revision}` | Adds a rule, or replaces what a held phrase meant. Operator confirms. |
| `vocabulary.forget {heard, revision}` | Removes the rule for a phrase. Operator confirms. |

- A change names the exact `revision` it read. A stale revision is refused and
  nothing is written. Every change advances the revision by one; a call that
  changes nothing (the same rule again, a phrase with no rule) says
  `changed: false` and writes nothing.
- A change applies from the next session. A session already open keeps the
  list it opened with, as it keeps its voice and its pause.
- Past transcripts are never rewritten.

## Where the list is kept

In the plugin's private store, as one file, `uid/corrections.json`, written by
the carrier through the host's private-file calls: staged, published by
compare-and-swap against the exact bytes that were read, then read back. A
change is reported as made only when the host said the publication is durable
and the same bytes read back. A publication that lost a race is refused; one
whose outcome is not known is reported as unresolved and is not retried.

Only a typed not-found means there is no list. A store that refuses or does not
answer is unavailable: the operations fail, and nothing is treated as empty.

At each session open the carrier reads the file inside the same bound as the
session's settings and hands its exact bytes to the worker beside them. No file
means no list. A store that does not yield the file in that time costs the
session its corrections, never its open, and the carrier logs one
`AII_VOICE_CORRECTIONS` line.

## Limits to know

- A stored file that this engine cannot read (it is written only by these
  operations, so this would take outside damage or a later engine with tighter
  bounds) is not listed, not written over, and has no operation that replaces
  it. Sessions run uncorrected and say so.
- A recognizer that cannot prefer terms (any but the multi-speaker recognizer
  the desktop sets ship) prefers none and says `"preferred": 0`; the rules
  still apply.
- Nothing shows the operator the uncorrected words yet; the engine sends them
  with every corrected transcript for a host that will.
