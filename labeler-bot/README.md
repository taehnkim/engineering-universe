# Jev labeler bot

This experiment uses `jev-1.13.0` as a first-pass labeler for ten pages. It
does not change the human labels in `data/learned_extraction/raw/annotations/`.

## Run the bot

Create a TypeSafe API key and put it in `labeler-bot/.env`:

```text
TYPESAFE_API_KEY=your-key
```

Then run:

```bash
uv run python labeler-bot/run.py
```

The run uses four train pages, three validation pages, and three test pages. It
removes scripts, styles, navigation, footers, forms, social controls, and other
page chrome. It keeps original DOM node IDs on up to 254 likely candidates.
Jev answers all four questions in one API request per page.

Use `--prepare-only` to inspect the cleaned inputs without an API key or API
calls. Use `--overwrite` to call Jev again for pages that already have bot
labels.

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

The generated data is ignored by Git. Each annotation has the same `labels`
map and original HTML hash as the core labeler. It also has Jev confidence,
choice probabilities, model name, token usage, and preparation statistics.
