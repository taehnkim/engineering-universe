# Learned DOM extraction v1

Version one is a node selector. Given HTML and one of `article`, `title`,
`author`, or `date`, it selects a single DOM element or the learned `missing`
option. Ordinary code returns the selected element's original-DOM HTML and
plain text. The model sees structural features and tag embeddings, **not the
article's words**.

## Labeling guide

All four fields must be either one candidate node ID or `null`:

- `article`: the tightest single wrapper containing the article body, including
  its headings, paragraphs, lists, code, tables, and content images. Exclude the
  headline, byline, and publication date when those have a separate wrapper.
- `title`: the one element wrapping the displayed article headline, normally an
  `h1`. Do not select the site name, browser `<title>`, or a card/link on an
  index page.
- `author`: the tightest byline wrapper containing the displayed author name or
  names. A wrapper may include an avatar or a literal “By”. Do not select a
  broad header that also contains the title/date.
- `date`: the tightest element containing the displayed publication date. Do
  not use an updated date when a distinct publication date is shown.

Use `null` only when the field is genuinely absent. Bootstrap output has
`review_status: "draft"` and is always excluded from training. Saving a page in
the labeling UI changes it to `review_status: "reviewed"`. Mark the whole page
`needs_review` when no clean single article wrapper exists, the rendered page is
an error/challenge, or the correct content is split across unrelated branches.
Review pages are excluded from training.

Correct article selections preserve nested code blocks, lists, tables, and
figures while avoiding recommendations, navigation, comments, and the footer.
Selecting only the paragraph text is too narrow; selecting `<main>` when it
also includes the title/byline and related stories is too broad. Correct title,
author, and date selections are the smallest semantic wrappers; selecting the
entire article header is too broad. Listing pages intentionally included in the
corpus should normally have all four fields set to `null`, not
`needs_review`—they are useful negative examples.

Annotations contain only positive selections and missing values, never labels
for incorrect candidates:

```json
{
  "page_id": "stripe-dev-001",
  "html_hash": "<sha256>",
  "needs_review": false,
  "review_status": "reviewed",
  "labels": {"article": 42, "title": 45, "author": 48, "date": null}
}
```

## Deterministic preprocessing

`eng_universe.extraction.dom.parse_html` uses Python's `html.parser` through
Beautiful Soup. It walks elements in document pre-order and assigns contiguous
IDs starting at zero. `head`, `script`, `style`, `noscript`, `template`, and
all their descendants are not selectable and do not consume IDs. The original
HTML string remains untouched; annotations and manifest rows contain its UTF-8
SHA-256 hash, so changed HTML invalidates labels.

For each candidate the model receives two categorical IDs (its lowercase tag
and its parent's lowercase tag) plus these seven numeric values:

1. `log1p` of Unicode character count in stripped descendant text.
2. `log1p` of descendant `p` count, including the candidate itself if it is a
   `p`.
3. Linked descendant-text characters divided by all descendant-text
   characters, clipped to `[0, 1]`.
4. `log1p` of element-ancestor count.
5. Candidate ID divided by `max(candidate_count - 1, 1)`.
6. One when the candidate itself has a `datetime` attribute, else zero.
7. One when descendant text matches the documented ISO/numeric or English
   month-name date pattern, else zero.

The five continuous columns are standardized with means and standard
deviations fit on training websites only. The tag vocabulary is also fit on
training websites only and reserves `<pad>` and `<unknown>`. Node IDs are for
bookkeeping/targets and never enter the model.

## Data, annotation, and training

The collector uses a persistent Chrome session through `browser-use`. It
combines every configured seed for a source, discovers additional URLs through
configured sitemaps when a listing is short, renders every selected page in
Chrome, and rejects duplicate canonical URLs. Its default target is 30 articles
per source plus listing-page negatives.

The checked-in raw corpus currently contains 757 rendered DOMs from 29
websites: 520 train, 206 validation, and 31 test. Whole websites belong to one
split only. Raw HTML, the manifest, and annotations are versioned; prepared
NumPy matrices, checkpoints, and evaluation output remain ignored because they
are reproducible build artifacts.

```bash
uv run python scripts/sample_extraction_html.py
uv run python scripts/bootstrap_extraction_annotations.py
uv run python -m eng_universe.extraction.annotation_app \
  --dataset-dir data/learned_extraction/raw
uv run python scripts/prepare_extraction_dataset.py
uv run python -m eng_universe.extraction.training \
  --prepared-dir data/learned_extraction/prepared \
  --output-dir data/learned_extraction/model
uv run python -m eng_universe.extraction.evaluation \
  --dataset-dir data/learned_extraction/raw \
  --checkpoint data/learned_extraction/model/best.pt \
  --output-dir data/learned_extraction/evaluation
```

The annotation page uses a sandboxed iframe without script permission. Hover
to highlight, choose a field and click an element, move to its parent when a
wrapper is too narrow, preview the chosen text, mark a field missing, or mark
the page for review.

The bootstrap command creates conservative drafts to accelerate labeling. All
drafts are excluded from preprocessing and training, even when the heuristic
is confident. Inspect every draft in the annotation UI and click Save to mark
it human-reviewed. Existing human-edited annotations are preserved unless
`--overwrite` is explicitly passed.

The current corpus has 751 bootstrap drafts and 6 human-reviewed annotations.
Do not train or report final accuracy until the desired split has been manually
reviewed. The older local checkpoint predates explicit review provenance and
was trained mostly on unverified bootstrap labels; it is intentionally not
versioned as a release artifact.

Training first tries to overfit four pages, then trains the full training split
and keeps the checkpoint with the lowest validation loss. Batches pad candidate
lists and mask padding. Each field applies cross-entropy over all real
candidates plus its learned missing score.

Evaluation is run only on held-out websites after checkpoint selection. It
reports per-field exact-node accuracy, missing precision/recall, missing and
unwanted word counts, per-website results, a heuristic baseline, checkpoint and
deployment size, peak process memory, latency, and an HTML failure inspector.
The first justified follow-up, if structurally similar candidates remain hard
to distinguish, is compact text/context features (for example class-token and
nearby-node embeddings), not a larger MLP or quantization.

## Inference

```python
from eng_universe.extraction import DOMExtractor

extractor = DOMExtractor("data/learned_extraction/model/best.pt")
result = extractor.extract(html, field="article")
# {"node_id": 42, "html": "<article>...</article>", "text": "..."} or None
```

For the convenience function with the requested two-argument shape, set
`ENG_UNIVERSE_EXTRACTOR_CHECKPOINT` once and call `extract(html, field)`.
