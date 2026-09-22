# Learned DOM extraction

Version one is a node selector. Given HTML and one of `article`, `title`,
`authors`, `date`, `summary`, or `relative_date`, it selects one DOM element or
the learned `missing` option. Ordinary code returns the selected element's
original-DOM HTML and plain text. The compact model combines DOM structure,
explicit author/date signals, and bounded semantic-token embeddings.

## Repository layout

This directory contains repository-only tooling used to build and inspect the
DOM extractor model:

- `dataset.py`, `training.py`, and `evaluation.py` prepare data, train a
  checkpoint, and measure it.
- `labeler_bot.py` and `apps/labeler.py` create and review annotations.
- `apps/playground.py` provides the local inference playground.
- `commands/` contains the sampling, migration, preparation, labeling, and
  smoke-test entry points.

Production extraction remains in `eng_universe/extraction/`. That package owns
the shared DOM cleanup, feature generation, model architecture, inference, and
postprocessing used by the application. Training and inference therefore use
one cleanup implementation rather than copies.

Install the repository's modeling-only dependencies with:

```bash
uv sync --group modeling
```

Put `TYPESAFE_API_KEY` in `modeling/dom_extractor/.env` for Jev labeling. The
repository ignores this file.

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
containers. Cleanup v2 also removes media (`img`, `svg`, `video`, `audio`,
`canvas`, `object`, `embed`, `source`, and `track`), hidden nodes, modal ARIA
roles, overlays, popups, and empty layout leaves. Semantic names are normalized
before matching, so `SiteFooter`, `site_footer`, and `site-footer` are treated
the same. Surviving elements keep their original IDs, so existing annotations
remain stable when their selected nodes survive. The original HTML string
remains untouched; annotations and manifest rows contain its UTF-8 SHA-256
hash, so changed HTML invalidates labels.

The labeler renders this same cleaned DOM instead of a separate raw view. The
prepared data and checkpoint record the `chrome-v2` cleanup version.
Inference rejects an older checkpoint instead of silently using different DOM
preprocessing. Training and inference always apply the same cleanup.

Feature schema `semantic-v3` gives each candidate five shared tag embeddings:
its own tag, parent, grandparent, previous sibling, and next sibling. It has two
independent semantic channels, so verbose attributes cannot displace useful
text evidence. The attribute channel accepts at most eight normalized
extraction roles from `class`, `id`, `itemprop`, `rel`, and `aria-label`. It
discards generic CSS utilities. The text channel accepts at most 12 phrases and
shapes such as `by:`, `written by`, a comma-delimited name list, a final
`and Name`, link count, date shape, and reading-time shape. Both channels use a
bounded 64-entry vocabulary and separate pooled representations. They do not
store arbitrary article prose or individual names.

Forty-nine numeric features cover size, depth, position,
paragraph/link/span counts, direct versus descendant text, leaf/wrapper shape,
capitalization, byline and profile markers, absolute and relative date
patterns, publication/update and reading-time markers, schema attributes, and
semantic ancestors. Relative-location features describe both directions from
the title and the start and end of the article subtree. This supports bylines
at the end of an article instead of assuming that every author is near its
title. Acknowledgement and contributor markers add evidence for those less
common attribution layouts.
Continuous columns are standardized from training websites only. Node IDs are
used only for bookkeeping and targets.

Prepared data and checkpoints record `semantic-v3`. Training and inference
reject older artifacts instead of silently applying a different feature
schema. Rebuild prepared data and retrain after changing the schema.

## Data, annotation, and training

The collector uses a persistent Chrome session through `browser-use`. It
combines every configured seed for a source, discovers additional URLs through
configured sitemaps when a listing is short, renders every selected page in
Chrome, and rejects duplicate canonical URLs. Its default target is 30 articles
per source plus listing-page negatives.

The current human-reviewed corpus contains 659 pages: 429 train, 200 validation,
and 30 test. Whole websites belong to one split only.
The root `.gitignore` excludes local data, prepared NumPy matrices, checkpoints,
and evaluation output.

```bash
uv run --group modeling python -m modeling.dom_extractor.commands.sample_html
uv run --group modeling python -m modeling.dom_extractor.commands.bootstrap_annotations
uv run --group modeling python -m modeling.dom_extractor.commands.migrate_schema
uv run --group modeling python -m modeling.dom_extractor.commands.label_with_jev --split train --limit 10
uv run --group modeling python -m modeling.dom_extractor.commands.label_with_jev --split validation --limit 10
# All 725 article pages, with five concurrent Jev requests:
uv run --group modeling python -m modeling.dom_extractor.commands.label_with_jev --limit 0 --concurrency 5
# All 757 pages, including 32 listing-page negatives:
uv run --group modeling python -m modeling.dom_extractor.commands.label_with_jev \
  --limit 0 --include-listings --concurrency 5
uv run --group modeling python -m modeling.dom_extractor.apps.labeler \
  --dataset-dir data/learned_extraction/raw
uv run --group modeling python -m modeling.dom_extractor.commands.prepare_dataset
uv run --group modeling python -m modeling.dom_extractor.training \
  --prepared-dir data/learned_extraction/prepared \
  --output-dir data/learned_extraction/model
uv run --group modeling python -m modeling.dom_extractor.evaluation \
  --dataset-dir data/learned_extraction/raw \
  --checkpoint data/learned_extraction/model/best.pt \
  --output-dir data/learned_extraction/evaluation
```

Human-reviewed annotations remain the default training gate. To run an explicit
pseudo-label experiment with reviewed labels plus Jev-backed drafts, keep its
artifacts separate:

```bash
uv run --group modeling python -m modeling.dom_extractor.commands.prepare_dataset \
  --output-dir data/learned_extraction/prepared_jev \
  --include-jev-drafts
uv run --group modeling python -m modeling.dom_extractor.training \
  --prepared-dir data/learned_extraction/prepared_jev \
  --output-dir data/learned_extraction/model_jev
uv run --group modeling python -m modeling.dom_extractor.evaluation \
  --dataset-dir data/learned_extraction/raw \
  --checkpoint data/learned_extraction/model_jev/best.pt \
  --output-dir data/learned_extraction/evaluation_jev \
  --include-jev-drafts
```

Metrics from this mode measure how well the small model reproduces Jev labels
on held-out websites. They are not human-verified extraction accuracy.

Start the read-only inference playground on port 8767:

```bash
uv run --group modeling python -m modeling.dom_extractor.apps.playground \
  --dataset-dir data/learned_extraction/raw \
  --checkpoint data/learned_extraction/model/best.pt
```

Choose a page and click **RUN**. The playground executes the checkpoint, shows
the predicted node for every field, compares it with the available human or Jev
reference, and jumps the rendered page to the selected prediction.

Open `/evals` on the same server for the whole-corpus dashboard. It runs the
checkpoint against every human-reviewed page across train, validation, and
test, and reports exact-node accuracy by field, site, and page. **RUN SITE**
evaluates only the selected website. Click a site row to show its indented page
results in place. Every site-table column is sortable. Expanded page titles open
that input in the playground, and each **[url]** link opens the source article.
Because this view includes training and validation pages, treat it as a fit and
data quality report rather than an unbiased generalization score.

Click **PLAYGROUND** on `/evals` to test an HTML file that is not in the corpus.
Choose a raw `.html` or `.htm` file up to 20 MB, then click **RUN**. The server
applies the same DOM cleanup used by training and inference, runs the selected
checkpoint, and displays the cleaned page with the predicted nodes highlighted.
Uploaded files are processed in memory and are not added to the dataset.

The dashboard uses ten parallel worker threads by default. While a run is
active, it shows a live `classified / total` count and progress bar. It stores
the latest result in browser local storage, keyed by the checkpoint and reviewed
labels. Returning from a page evaluation restores that result without another
whole-corpus run. Click **RUN ALL** or **RUN SITE** to refresh it. Set another
bounded worker count when you start the app with `--eval-workers`, for example
`--eval-workers 2` on a machine with limited CPU or memory.

The annotation page uses a sandboxed iframe without script permission. Hover
to highlight, choose a field and click an element, move to its parent when a
wrapper is too narrow, preview the chosen text, mark a field missing, or mark
the page for review.

The bootstrap command creates conservative fallback drafts. `LabelerBot` then
uses Jev to replace each selected fallback draft before human review. It keeps
the full Jev result in `raw/jev_annotations/` and copies the selected node IDs
to the core annotation. It reuses matching results from that audit directory
without another API call. It does not replace a human-reviewed annotation
unless `--overwrite-reviewed` is explicit. All drafts are excluded from
preprocessing and training, even when Jev is confident.

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
another node, choose missing, or move to a parent. **Rerun Jev** makes a new
single-field API request, updates that card with the new node, confidence, and
field latency, and refreshes the Jev audit file. The returned choice remains an
unsaved human edit until **Save** is clicked.

The collector records `scraped_at` when it fetches each page. After extraction,
ordinary code keeps an absolute `date` unchanged. If only `relative_date` is
present, it subtracts that duration from `scraped_at` and stores the result as
`published_at`.

Training first tries to overfit four pages, then trains the full training split
and keeps the checkpoint with the lowest validation loss. Batches pad candidate
lists and mask padding. Each field applies cross-entropy over all real
candidates plus its learned missing score. Author loss has weight 3 and date
loss has weight 2, so good article/title performance cannot hide weak metadata
selection.

Evaluation is run only on held-out websites after checkpoint selection. It
reports per-field exact-node accuracy, missing precision/recall, missing and
unwanted word counts, per-website results, checkpoint and deployment size, peak
process memory, latency, and an HTML failure inspector. Exact-node accuracy is
strict: a semantically correct parent or child wrapper still counts as wrong,
so inspect word errors and failure HTML when diagnosing broad selections.

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
