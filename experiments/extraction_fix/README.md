# parse_html extraction fix eval

Verifies `__NEXT_DATA__` / JSON-LD body extraction in `eng_universe.ingest.etl.parse_html`.

## Run

```bash
uv run pytest tests/ingest/test_etl_parse_html.py -q
uv run python experiments/extraction_fix/run_eval.py
```

Open `outputs/index.html` for per-fixture parsed bodies.

## Fixtures

| Fixture | Source | How obtained |
|---------|--------|--------------|
| `stripe_doing_more.html` | stripe.dev | live curl (SSR shell + `__NEXT_DATA__`) |
| `notion_article.html` | notion.com/blog | live curl |
| `openai_article.html` | developers.openai.com/blog | live curl |
| `ramp_article.html` | builders.ramp.com | **browser Save Page** (Vite SPA; curl is empty shell) |
| `airbnb_medium.html` | Medium Airbnb Eng | **browser Save Page** (curl hits Cloudflare) |
