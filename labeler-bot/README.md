# Jev labeler bot

`LabelerBot` uses `jev-1.13.0` as the first-pass labeler for ten pages. Its
implementation is the importable
`eng_universe.extraction.labeler_bot.LabelerBot` module.

## Run the bot

Create a TypeSafe API key and put it in `labeler-bot/.env`:

```text
TYPESAFE_API_KEY=your-key
```

Then run:

```bash
uv run python labeler-bot/run.py
```

For the core training dataset, use the split-aware tool instead:

```bash
uv run python scripts/label_extraction_with_jev.py --split train --limit 10
uv run python scripts/label_extraction_with_jev.py --split validation --limit 10
```

Use `--limit 0` to process every page in the selected split. Core audit data is
stored in `data/learned_extraction/raw/jev_annotations/` and appears in the
editable human labeler on port 8765.

The core command is concurrent and resumable. It uses the TypeSafe SDK's async
client with five requests at a time, retries rate limits and transient server
failures, and saves every completed result immediately. Adjust the bounded
worker count with `--concurrency`; cached pages do not make another API call.

```bash
# All 725 articles in train, validation, and test:
uv run python scripts/label_extraction_with_jev.py --limit 0 --concurrency 5

# All 757 HTML files, including listing-page negatives:
uv run python scripts/label_extraction_with_jev.py \
  --limit 0 --include-listings --concurrency 5
```

The run uses four train pages, three validation pages, and three test pages. It
removes scripts, styles, navigation, footers, forms, social controls, and other
page chrome. This is the same `chrome-v1` cleanup used by small-model training
and inference. It keeps original DOM node IDs on up to 254 likely candidates.
Jev answers all six questions in one API request per page: article, title,
authors, absolute date, summary, and relative date. Reading durations such as
`5 min read` are not relative dates.

Each result is stored in `labeler-bot/data` with its confidence metadata. The
six selections also replace the matching core annotation when that annotation
is not human-reviewed. The core copy is a draft with `needs_review: true`, so
the small model cannot train on it until a person saves it in the human
labeler. A reviewed human annotation is never overwritten.

Use `--prepare-only` to inspect the cleaned inputs without an API key or API
calls. Use `--overwrite` to call Jev again for pages that already have bot
labels. Use `--separate-only` to keep a run out of the core annotation drafts.

## Review the result

Start the read-only review UI:

```bash
uv run python labeler-bot/app.py
```

Open [http://127.0.0.1:8766/](http://127.0.0.1:8766/). Select a field to scroll
to the chosen node. The right panel shows Jev's confidence and a content
preview. It also links to the exact prepared HTML sent to Jev.

Generated files stay under `labeler-bot/data/`:

```text
labeler-bot/data/
|-- run.json
|-- prepared/
|   `-- <page-id>.html
`-- annotations/
    `-- <page-id>.json
```

The generated data is ignored by Git. Each bot annotation has the same
`labels` map and original HTML hash as the core labeler. It also has Jev
confidence, choice probabilities, model name, token usage, and preparation
statistics. The terminal and review UI also show request latency in
milliseconds.
