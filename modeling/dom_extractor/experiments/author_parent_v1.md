# Author parent-versus-child experiment

This experiment tests whether the small author-boundary ranker can prefer the exact byline node to a broader parent. It does not change the base model, runtime features, the npm package, or reviewed labels.

The training command finds training pages where the current ranker chose an ancestor of the human-labeled author node. A broad parent must add at least two words or a date. It fine-tunes the existing ranker with an extra loss that pushes the labeled node above that parent. It selects an epoch and decision margin on validation data only. It writes a candidate checkpoint and a JSON report to a separate output directory; it does not install either artifact as the default.

```bash
uv run python -m modeling.dom_extractor.commands.experiment_author_boundaries \
  --dataset-dir data/learned_extraction/raw \
  --base-checkpoint eng_universe/extraction/checkpoints/best.pt \
  --reference-checkpoint eng_universe/extraction/checkpoints/author_boundary.pt \
  --output-dir data/learned_extraction/experiments/author_parent_v1
```

Compare all four fields before considering a candidate:

```bash
uv run python -m modeling.dom_extractor.commands.compare_checkpoints \
  --dataset-dir data/learned_extraction/raw \
  --reference-base eng_universe/extraction/checkpoints/best.pt \
  --reference-author eng_universe/extraction/checkpoints/author_boundary.pt \
  --candidate-base eng_universe/extraction/checkpoints/best.pt \
  --candidate-author data/learned_extraction/experiments/author_parent_v1/author_boundary.pt \
  --output data/learned_extraction/experiments/author_parent_v1/full_field_comparison.json
```

On the current 659 reviewed pages, the focused run fixed three training pages but made no change on validation or the held-out test site. It failed the promotion gate. The checkpoint in this experiment is **not** the default and must not replace the bundled checkpoint based on training accuracy alone. The source labels include conflicting author-boundary policies on some Shopify and Stripe pages; those need human review before a more aggressive boundary model can be trusted.
