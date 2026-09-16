# Trafilatura cleanup evaluation

Standalone experiment: raw eng-blog HTML → Trafilatura cleaned HTML/XML.

## Run

```bash
uv run python experiments/trafilatura_eval/run_eval.py
```

Open `outputs/index.html` for side-by-side input/output views.

## Fixtures

Live-fetched samples under `fixtures/` (Stripe.dev index + articles known to put body in `__NEXT_DATA__`, plus one Shopify Engineering page with a normal DOM body). R2 `raw/` was not available in the eval environment.

This does **not** change `parse_html` in the main pipeline.
