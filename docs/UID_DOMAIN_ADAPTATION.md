# Separated-speech identity adaptation gate

This is an offline development gate, not an installed UID feature or a new
decision policy. A correctly transcribed, separated voice can still have an
embedding too distorted to match its clean enrollment. More models or a lower
acceptance margin are not evidence that this problem is repaired.

`scripts.uid_domain_adapter` tests the smallest learned correction: a linear
residual map from separated-query embeddings toward the clean embeddings of
the same speech. The enrollment gallery is unchanged. Clean-control and
identity-shrinkage penalties constrain drift. The fit has no speaker-specific
parameters and does not use a label or UUID at inference. Its positive ridge
term gives a unique solution even with rank-deficient data. This is embedding
adaptation, not fine-tuning the acoustic encoder.

## Data and decision boundaries

1. Bind the source, models, native frontend, recording manifest and decision
   policy before collection. Check them again when the run finishes.
2. Use different speakers for fitting, selecting a correction and evaluation.
   `disjoint_partitions` rejects overlapping or empty speaker partitions.
   A previously examined evaluation set is a regression set, not a new blind
   benchmark. Public development data repurposed for fitting must be named as
   training data for this run.
3. Generate mixtures without using recognition scores. Run the real separator
   and recognizer. Pair the recognizer's exact selected evidence regions with
   the corresponding clean recording regions. Clean teacher audio is available
   only in this offline training/assessment, never a production dependency.
   Ground truth resolves source permutations for supervision and grading only.
4. Fit only training pairs. Select among predeclared regularization strengths
   on the separate validation speakers. Hold identification threshold, competing
   profile margin and original enrollment construction fixed. A correction that
   improves angular reconstruction but accepts an unknown speaker or loses
   known coverage is not eligible.
5. Freeze one correction before evaluation. Check clean speech, quiet overlap,
   unknown speakers and the exact existing recognizer-selected failures.
   Missing text/evidence and short evidence stay in the coverage denominator;
   they are not discarded because they cannot be fitted. Repeated gains or
   utterances are correlated observations, not independent speakers.
6. Report failure as failure. Synthetic unit tests prove the optimization and
   numerical refusals only. Neither a lower training loss nor whole-waveform
   accuracy qualifies the actual selected-audio path.

Any successful correction would still need an explicit bound inference
artifact, a model/recipe fingerprint, native numerical parity, resource and
lifecycle checks, then installed tests. It must not be injected behind an
unchanged embedding binding or silently rewrite existing profiles. It is not
enabled in the worker, package, or plugin settings.

Run the model-free mathematical contracts with:

```sh
python -m pytest tests/qualify_uid_domain_adapter.py
```

Waveforms, embeddings and fitted matrices stay outside the source tree.
