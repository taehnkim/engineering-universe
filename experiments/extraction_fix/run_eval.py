#!/usr/bin/env python3
"""Compare parse_html output across eng-blog fixtures (Stripe NEXT_DATA fix)."""

from __future__ import annotations

import json
from pathlib import Path

from eng_universe.ingest.etl import parse_html

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
OUTPUTS = ROOT / "outputs"

FIXTURE_URLS = {
    "stripe_doing_more.html": (
        "https://stripe.dev/blog/doing-more-with-less-reducing-requests-to-the-stripe-api"
    ),
    "notion_article.html": (
        "https://www.notion.com/blog/building-observability-into-notions-dead-letter-queue"
    ),
    "openai_article.html": (
        "https://developers.openai.com/blog/15-lessons-building-chatgpt-apps"
    ),
    "ramp_article.html": (
        "https://builders.ramp.com/post/how-we-built-oca-our-ai-on-call-assistant"
    ),
    "airbnb_medium.html": (
        "https://medium.com/airbnb-engineering/beyond-the-model-engineering-ai-infra-with-scientific-judgement-371316d43261"
    ),
}

# Markers that should appear if extraction recovered the real article.
EXPECTED_MARKERS = {
    "stripe_doing_more.html": "It is important for developers to optimize third-party API usage",
    "notion_article.html": "dead-letter",
    "openai_article.html": "ChatGPT",
    "ramp_article.html": "OCA",
    "airbnb_medium.html": "scientific",
}


def main() -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, url in FIXTURE_URLS.items():
        path = FIXTURES / name
        if not path.exists():
            rows.append(
                {
                    "fixture": name,
                    "url": url,
                    "status": "missing_fixture",
                }
            )
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_html(url, html)
        marker = EXPECTED_MARKERS.get(name, "")
        ok = bool(marker) and marker.lower() in parsed.content.lower()
        out = {
            "fixture": name,
            "url": url,
            "status": "ok" if ok else "marker_miss",
            "title": parsed.title,
            "content_chars": len(parsed.content),
            "marker": marker,
            "marker_found": ok,
            "content_snippet": parsed.content[:600],
            "has_next_data": "__NEXT_DATA__" in html,
        }
        (OUTPUTS / f"{path.stem}.parsed.txt").write_text(
            f"TITLE: {parsed.title}\nURL: {url}\nCHARS: {len(parsed.content)}\n"
            f"MARKER_FOUND: {ok}\n\n{parsed.content}\n",
            encoding="utf-8",
        )
        # Side-by-side HTML for screenshots
        compare = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>parse_html — {name}</title>
<style>
body{{font-family:IBM Plex Sans,Segoe UI,sans-serif;margin:1rem;background:#f4f1ea;color:#1c1a16}}
.badge{{display:inline-block;padding:.2rem .5rem;margin-right:.4rem;border:1px solid #ccc;border-radius:4px;background:#fff}}
.ok{{border-color:#7bb98a;color:#0b6e4f}}.bad{{border-color:#e2a080;color:#8a3b12}}
pre{{white-space:pre-wrap;background:#fff;border:1px solid #ddd;padding:1rem;max-height:32rem;overflow:auto}}
</style></head><body>
<h1>parse_html result</h1>
<p><strong>{name}</strong><br/>{url}</p>
<p>
<span class="badge {'ok' if ok else 'bad'}">{'marker found' if ok else 'marker MISS'}</span>
<span class="badge">{len(parsed.content)} chars</span>
<span class="badge">{'__NEXT_DATA__' if '__NEXT_DATA__' in html else 'no NEXT_DATA'}</span>
</p>
<h2>Title</h2><p>{parsed.title}</p>
<h2>Parsed body</h2>
<pre>{parsed.content[:8000].replace('&','&amp;').replace('<','&lt;')}</pre>
</body></html>
"""
        (OUTPUTS / f"{path.stem}.compare.html").write_text(compare, encoding="utf-8")
        rows.append(out)
        print(f"{name}: chars={len(parsed.content)} marker_found={ok}")

    (OUTPUTS / "summary.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    index_rows = "".join(
        f"<tr><td><a href='{Path(r['fixture']).stem}.compare.html'>{r['fixture']}</a></td>"
        f"<td>{r.get('status')}</td><td>{r.get('content_chars','')}</td>"
        f"<td>{'yes' if r.get('marker_found') else 'no'}</td></tr>"
        for r in rows
    )
    (OUTPUTS / "index.html").write_text(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Extraction fix eval</title>
<style>body{{font-family:sans-serif;margin:1.5rem}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.5rem}}</style>
</head><body><h1>parse_html multi-site check</h1>
<table><tr><th>Fixture</th><th>Status</th><th>Chars</th><th>Marker</th></tr>{index_rows}</table>
</body></html>""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
