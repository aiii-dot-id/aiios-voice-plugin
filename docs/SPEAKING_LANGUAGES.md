# Speaking languages

The speaking-language setting chooses the speech model a session speaks with.
Recognition is a different model and stays English: this setting does not
change what the engine understands.

## What a language is

Each language is its own compact speech model with its own tokenizer,
configuration and voice presets. English is the model at the root of the bound
speech directory, where it has always been. Every other language is a
directory beside it:

    tts/model.safetensors, tokenizer.model, config.yaml, embeddings/      English
    tts/languages/<name>/model.safetensors, tokenizer.model,
                         config.yaml, embeddings/<voice>.safetensors      one more language

`<name>` is `french`, `german`, `spanish`, `italian`, `portuguese` or `dutch`.
A voice preset is computed for one model's weights, so every language carries
its own file for each voice it offers; a preset from another language's
directory is never used.

## Choosing one

The setting is pinned for the session. The voice is not: a saved voice is
taken at the first segment of the next reply. When a session asks for
a language other than the one loaded, the engine releases the resident speech
model and loads the requested one before the session opens; one speech model
is resident at a time. A language is refused, and only that session fails,
when:

- its model, tokenizer or configuration is not installed, or the selected
  voice's preset for that language is not (a language still being fetched has
  some of its files and not others; that is not installed, for this session);
- its configuration's `replace_characters` block is not in a form this
  engine reads (quoted scalars, no escapes);
- its model does not load, in which case the previous language's model is put
  back first.

If neither the requested nor the previous model loads, the engine has no
speech model. That is an engine failure and is reported as one.

There is no automatic language choice and no fallback to another language.

## Text

A model's configuration lists the characters its training text never contained
and what stands in for each. The engine applies that table to each reply
segment just before synthesis. A segment made only of removed characters is
silent and completes normally.

## What is and is not established

The declaration, the selection path and the per-session refusal are covered by
the session tests.

On Ubuntu with an NVIDIA device (Vulkan), the engine built from this source
spoke one sentence in each of the seven languages in one process, switching
language at session open ten times, English three times with identical
samples each time. A switch added 0.2 to 0.5 s to the session open. The
publisher's six configurations are read by this engine's replacement reader as
written. Each language's two tokenizer files (`tokenizer.model`, read here,
and `tokenizer.json`, named by the configuration) hold the same 4,000 pieces
with the same scores.

Not established: that the speech is correct and natural to a speaker of each
language (no listening judgment and no recognition check has been made); any
language on macOS or Windows; the cost of a switch on CPU or Metal; behaviour
under interruption and recovery in a language other than English. Until a
package carries a language's files, choosing it refuses the session.
