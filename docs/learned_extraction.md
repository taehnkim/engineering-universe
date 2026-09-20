# Learned DOM extraction v1

Version one is a node selector. Given HTML and one of `article`, `title`,
`authors`, `date`, `summary`, or `relative_date`, it selects one DOM element or
the learned `missing` option. Ordinary code returns the selected element's
original-DOM HTML and plain text. The model sees structural features and tag
embeddings, **not the article's words**.

## Labeling guide

All six fields must be either one candidate node ID or `null`:

- `article`: the tightest single wrapper containing the article body, including
  its headings, paragraphs, lists, code, tables, and content images. Exclude the
  headline, byline, and publication date when those have a separate wrapper.
- `title`: the one element wrapping the displayed article headline, normally an
  `h1`. Do not select the site name, browser `<title>`, or a card/link on an
  index page.
- `authors`: the tightest byline wrapper that contains all displayed author
  names. A wrapper may include an avatar or a literal “By”. Do not select one
  author link when the article has several authors. Do not select a broad
  header that also contains the title or date.
- `date`: the tightest element containing the displayed publication date. Do
  not use an updated date when a distinct publication date is shown. Use this
  field only for an absolute date.
- `summary`: the subtitle, standfirst, deck, or short summary that belongs to
  the title. Use `null` when the page has no such element.
- `relative_date`: the tightest element containing a relative publication date
  such as `2 days ago`. A reading time such as `5 min read` is not a relative
  date.

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
authors, date, summary, and relative-date selections are the smallest semantic
wrappers; selecting the entire article header is too broad. Listing pages
intentionally included in the corpus should normally have all six fields set
to `null`, not
`needs_review`—they are useful negative examples.

Annotations contain only positive selections and missing values, never labels
for incorrect candidates:

```json
{
  "page_id": "stripe-dev-001",
  "html_hash": "<sha256>",
  "needs_review": false,
  "review_status": "reviewed",
  "labels": {
    "article": 42,
    "title": 45,
    "authors": 48,
    "date": null,
    "summary": 46,
    "relative_date": null
  }
}
```

## Deterministic preprocessing

`eng_universe.extraction.dom.parse_html` uses Python's `html.parser` through
Beautiful Soup. It walks elements in document pre-order and assigns stable IDs
starting at zero. Before feature generation, it removes common page chrome:
navigation, footers, forms, dialogs, cookie and consent UI, menus,
recommendations, related-content blocks, share controls, and similar
containers. Surviving elements keep their original IDs, so annotations and
returned original-DOM content remain valid. The original HTML string remains
untouched; annotations and manifest rows contain its UTF-8 SHA-256 hash, so
changed HTML invalidates labels.

The prepared data and checkpoint record the `chrome-v1` cleanup version.
Inference rejects an older checkpoint instead of silently using different DOM
preprocessing. Training and inference always apply the same cleanup.

For each candidate the model receives two categorical IDs (its lowercase tag
and its parent's lowercase tag) plus these eight numeric values:

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
8. One when descendant text contains a relative publication date, else zero.

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

The local raw corpus currently contains 757 rendered DOMs from 29 websites: 520
train, 206 validation, and 31 test. Whole websites belong to one split only.
The root `.gitignore` excludes local data, prepared NumPy matrices, checkpoints,
and evaluation output.

```bash
uv run python scripts/sample_extraction_html.py
uv run python scripts/bootstrap_extraction_annotations.py
uv run python scripts/migrate_extraction_schema.py
uv run python scripts/label_extraction_with_jev.py --split train --limit 10
uv run python scripts/label_extraction_with_jev.py --split validation --limit 10
# All 725 article pages, with five concurrent Jev requests:
uv run python scripts/label_extraction_with_jev.py --limit 0 --concurrency 5
# All 757 pages, including 32 listing-page negatives:
uv run python scripts/label_extraction_with_jev.py \
  --limit 0 --include-listings --concurrency 5
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

Human-reviewed annotations remain the default training gate. To run an explicit
pseudo-label experiment with reviewed labels plus Jev-backed drafts, keep its
artifacts separate:

```bash
uv run python scripts/prepare_extraction_dataset.py \
  --output-dir data/learned_extraction/prepared_jev \
  --include-jev-drafts
uv run python -m eng_universe.extraction.training \
  --prepared-dir data/learned_extraction/prepared_jev \
  --output-dir data/learned_extraction/model_jev
uv run python -m eng_universe.extraction.evaluation \
  --dataset-dir data/learned_extraction/raw \
  --checkpoint data/learned_extraction/model_jev/best.pt \
  --output-dir data/learned_extraction/evaluation_jev \
  --include-jev-drafts
```

Metrics from this mode measure how well the small model reproduces Jev labels
on held-out websites. They are not human-verified extraction accuracy.

The annotation page uses a sandboxed iframe without script permission. Hover
to highlight, choose a field and click an element, move to its parent when a
wrapper is too narrow, preview the chosen text, mark a field missing, or mark
the page for review.

The bootstrap command creates conservative fallback drafts. `LabelerBot` then
uses Jev to replace each selected fallback draft before human review. It keeps
the full Jev result in `raw/jev_annotations/` and copies the selected node IDs
to the core annotation. It can reuse earlier results from
`labeler-bot/data/annotations/` without another API call. It does not replace a
human-reviewed annotation unless `--overwrite-reviewed` is explicit. All drafts
are excluded from preprocessing and training, even when Jev is confident.

The core Jev command uses the TypeSafe SDK's asynchronous client. It runs five
requests at a time by default, supports `--concurrency 1` through `32`, retries
rate limits and transient server failures, and respects the server's
`Retry-After` response. It writes each completed audit result immediately, so a
later run resumes from the cache instead of paying for the same page again.
`--limit 0` selects every article in the requested splits. Add
`--include-listings` to classify the listing-page negatives too; Jev should mark
their absent article fields as missing.

The core annotation UI can filter by train, validation, or test split and by
whether Jev supplied a first pass. A diamond marks pages with Jev audit data.
Each field shows Jev's confidence and original node choice. A person can select
another node, choose missing, move to a parent, or restore the Jev choice before
saving the reviewed annotation.

The collector records `scraped_at` when it fetches each page. After extraction,
ordinary code keeps an absolute `date` unchanged. If only `relative_date` is
present, it subtracts that duration from `scraped_at` and stores the result as
`published_at`.

The current local corpus has 750 bootstrap drafts and 7 human-reviewed
annotations. Only 2 reviewed pages are in the training split, 5 are in the
validation split, and none are in the test split. A local six-field checkpoint
can confirm that the pipeline works. Do not treat its validation score as a
useful quality estimate, and do not report held-out accuracy until test pages
have been reviewed.

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

document = extractor.extract_document(html, scraped_at="2026-09-19T12:00:00Z")
# document.authors is one string containing the full byline.
# document.published_at uses date, or relative_date resolved from scraped_at.
```

For the convenience function with the requested two-argument shape, set
`ENG_UNIVERSE_EXTRACTOR_CHECKPOINT` once and call `extract(html, field)`.
