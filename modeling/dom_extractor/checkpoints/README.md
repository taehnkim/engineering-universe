# Reviewed-corpus checkpoint candidates

`reviewed-2026-09-25-warmstart.pt` is a candidate trained on the 529-page
reviewed training split. It initializes from the current bundled checkpoint and
reuses that checkpoint's vocabulary and feature normalizer. It is **not** the
runtime default: its author accuracy regressed on the 250-page validation set.

To try it in Python, pass this path as the checkpoint and omit an author-boundary
checkpoint. The bundled author-boundary model is tied to the bundled base
checkpoint and must not be paired with this candidate.

See [the experiment report](../experiments/reviewed-2026-09-25.md) for the
split, training commands, and validation and test results.
