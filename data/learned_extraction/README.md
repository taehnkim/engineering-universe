# Learned extraction data

This directory contains the datasets and generated artifacts for the learned
DOM-node extractor.

## `raw/`

The source-of-truth dataset:

- Chrome-rendered HTML files
- `manifest.json`, including train, validation, and test assignments
- Annotation JSON files edited by the labeler

This directory is committed to Git.

## `prepared/`

Machine-ready data generated from reviewed annotations:

- Numeric DOM features
- Target node IDs
- Tag vocabulary
- Feature-normalization parameters

Regenerate it with:

```bash
uv run python scripts/prepare_extraction_dataset.py
```

## `model/`

Training output, including the `best.pt` checkpoint and training metrics.
These files are generated locally and ignored by Git.

## `evaluation/`

Evaluation output, including accuracy reports, per-field and per-website
metrics, and the HTML failure inspector. These files are also generated locally
and ignored by Git.

Only `raw/` and this README are versioned. `prepared/`, `model/`, and
`evaluation/` are reproducible build artifacts.
