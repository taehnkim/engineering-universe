#!/usr/bin/env python3
"""Standalone Trafilatura evaluation for eng-blog raw HTML.

Goal: raw HTML in → cleaned HTML/XML out with semantic DOM tags preserved
(not text-only). Also surface Stripe.dev __NEXT_DATA__ failure mode.

Does not modify eng_universe.ingest.etl.parse_html.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from trafilatura import extract

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
OUTPUTS = ROOT / "outputs"

# Live-fetched samples (see fixtures/). R2 raw/ not available in this env.
FIXTURE_URLS: dict[str, str] = {
    "stripe_blog_index.html": "https://stripe.dev/blog",
    "stripe_doing_more_with_less.html": (
        "https://stripe.dev/blog/doing-more-with-less-reducing-requests-to-the-stripe-api"
    ),
    "stripe_because_nobody_likes.html": (
        "https://stripe.dev/blog/because-nobody-likes-being-charged-twice"
    ),
    "stripe_adding_payments_agentic.html": (
        "https://stripe.dev/blog/adding-payments-to-your-agentic-workflows"
    ),
    "shopify_eng_sample.html": (
        "https://shopify.engineering/building-resilient-payment-systems"
    ),
}

OUTPUT_FORMATS = ("txt", "html", "xml", "markdown")

SEMANTIC_TAG_RE = re.compile(
    r"<(p|h[1-6]|ul|ol|li|pre|code|blockquote|table|thead|tbody|tr|th|td|a|em|strong|b|i)\b",
    re.I,
)
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.S,
)


@dataclass
class FixtureReport:
    fixture: str
    url: str
    input_bytes: int
    has_next_data: bool
    next_data_content_chars: int
    next_data_content_snippet: str
    article_body_dom_text_chars: int
    outputs: dict[str, dict]


def _find_large_content_strings(obj: object, path: str = "") -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else key
            if key in {"content", "body", "html", "markdown"} and isinstance(value, str):
                if len(value) > 200:
                    found.append((child, len(value), value[:160].replace("\n", " ")))
            found.extend(_find_large_content_strings(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj[:40]):
            found.extend(_find_large_content_strings(value, f"{path}[{index}]"))
    return found


def inspect_next_data(html: str) -> tuple[bool, int, str]:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return False, 0, ""
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return True, 0, "(unparseable __NEXT_DATA__)"
    found = _find_large_content_strings(data)
    if not found:
        return True, 0, ""
    # Prefer postData.content-style paths when present.
    found.sort(key=lambda item: item[1], reverse=True)
    _path, length, snippet = found[0]
    return True, length, snippet


def article_body_dom_text_chars(html: str) -> int:
    # Lightweight check: CSS-module class names containing articleBody.
    match = re.search(
        r'class="[^"]*articleBody[^"]*"[^>]*>(.*?)</div>',
        html,
        re.S | re.I,
    )
    if not match:
        return -1
    inner = re.sub(r"<[^>]+>", "", match.group(1))
    return len(inner.strip())


def analyze_output(text: str | None, next_snippet: str) -> dict:
    if not text:
        return {
            "chars": 0,
            "semantic_tag_hits": 0,
            "semantic_tags_sample": [],
            "contains_next_data_prose": False,
            "snippet": "",
        }
    tags = sorted({m.group(1).lower() for m in SEMANTIC_TAG_RE.finditer(text)})
    marker = next_snippet.strip()[:48] if next_snippet.strip() else ""
    contains = bool(marker) and marker in text
    return {
        "chars": len(text),
        "semantic_tag_hits": len(SEMANTIC_TAG_RE.findall(text)),
        "semantic_tags_sample": tags,
        "contains_next_data_prose": contains,
        "snippet": text[:500],
    }


def run_fixture(path: Path) -> FixtureReport:
    html = path.read_text(encoding="utf-8", errors="replace")
    url = FIXTURE_URLS.get(path.name, "")
    has_next, next_len, next_snip = inspect_next_data(html)
    outputs: dict[str, dict] = {}
    for fmt in OUTPUT_FORMATS:
        extracted = extract(
            html,
            url=url or None,
            output_format=fmt,
            with_metadata=True,
            include_formatting=True,
            include_links=True,
            include_tables=True,
            include_images=False,
            include_comments=False,
            favor_recall=True,
        )
        out_name = f"{path.stem}.{fmt}.out"
        (OUTPUTS / out_name).write_text(extracted or "", encoding="utf-8")
        outputs[fmt] = {
            "file": out_name,
            **analyze_output(extracted, next_snip),
        }
    return FixtureReport(
        fixture=path.name,
        url=url,
        input_bytes=len(html.encode("utf-8")),
        has_next_data=has_next,
        next_data_content_chars=next_len,
        next_data_content_snippet=next_snip,
        article_body_dom_text_chars=article_body_dom_text_chars(html),
        outputs=outputs,
    )


def write_side_by_side(report: FixtureReport, html: str) -> None:
    """Write a human-readable comparison for walkthrough screenshots."""
    input_snip = html[:2500]
    html_out = (OUTPUTS / report.outputs["html"]["file"]).read_text(encoding="utf-8")
    xml_out = (OUTPUTS / report.outputs["xml"]["file"]).read_text(encoding="utf-8")
    txt_out = (OUTPUTS / report.outputs["txt"]["file"]).read_text(encoding="utf-8")

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Trafilatura eval — {esc(report.fixture)}</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --ink: #1c1a16;
      --muted: #5c564c;
      --panel: #fffdf8;
      --accent: #0b6e4f;
      --warn: #8a3b12;
      --line: #d7d0c2;
    }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 10% 0%, #e7f0ea 0%, transparent 45%),
        linear-gradient(180deg, #efe8d8, var(--bg));
    }}
    header {{
      padding: 1.25rem 1.5rem 0.5rem;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 0.35rem; font-size: 1.35rem; }}
    .meta {{ color: var(--muted); font-size: 0.92rem; }}
    .badge {{
      display: inline-block;
      margin-right: 0.4rem;
      padding: 0.15rem 0.45rem;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--panel);
      font-size: 0.8rem;
    }}
    .warn {{ color: var(--warn); border-color: #e2b08f; }}
    .ok {{ color: var(--accent); border-color: #9dceb8; }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
      padding: 0.75rem 1rem 1.5rem;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      min-height: 18rem;
    }}
    section h2 {{
      margin: 0;
      padding: 0.55rem 0.75rem;
      font-size: 0.95rem;
      border-bottom: 1px solid var(--line);
      background: #f7f2e8;
    }}
    pre {{
      margin: 0;
      padding: 0.75rem;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 0.78rem;
      line-height: 1.35;
      max-height: 28rem;
      overflow: auto;
    }}
    .rendered {{
      padding: 0.85rem 1rem;
      max-height: 28rem;
      overflow: auto;
      font-size: 0.92rem;
      line-height: 1.45;
    }}
    @media (max-width: 900px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Trafilatura raw → cleaned HTML</h1>
    <div class="meta">
      <div><strong>Fixture:</strong> {esc(report.fixture)}</div>
      <div><strong>URL:</strong> {esc(report.url)}</div>
      <div style="margin-top:0.45rem">
        <span class="badge">input {report.input_bytes:,} B</span>
        <span class="badge {'ok' if report.has_next_data else ''}">{esc('__NEXT_DATA__' if report.has_next_data else 'no __NEXT_DATA__')}</span>
        <span class="badge {'warn' if report.next_data_content_chars else ''}">{esc(f'NEXT content {report.next_data_content_chars:,} chars' if report.next_data_content_chars else 'no embedded content field')}</span>
        <span class="badge {'warn' if report.article_body_dom_text_chars == 0 else ''}">{esc(f'articleBody DOM text={report.article_body_dom_text_chars}')}</span>
        <span class="badge">{esc('html tags: ' + ', '.join(report.outputs['html']['semantic_tags_sample'] or ['(none)']))}</span>
        <span class="badge {'warn' if report.next_data_content_chars and not report.outputs['html']['contains_next_data_prose'] else 'ok'}">{esc('NEXT prose recovered' if report.outputs['html']['contains_next_data_prose'] else 'NEXT prose NOT in output')}</span>
      </div>
    </div>
  </header>
  <div class="grid">
    <section>
      <h2>Input HTML snippet (raw)</h2>
      <pre>{esc(input_snip)}</pre>
    </section>
    <section>
      <h2>Output HTML (trafilatura output_format=&quot;html&quot;)</h2>
      <pre>{esc(html_out[:4000] if html_out else '(empty)')}</pre>
    </section>
    <section>
      <h2>Output XML (structure-preserving)</h2>
      <pre>{esc(xml_out[:4000] if xml_out else '(empty)')}</pre>
    </section>
    <section>
      <h2>Output text (baseline)</h2>
      <pre>{esc(txt_out[:3000] if txt_out else '(empty)')}</pre>
    </section>
    <section style="grid-column: 1 / -1">
      <h2>Rendered cleaned HTML (browser view)</h2>
      <div class="rendered">{html_out if html_out else '<em>(empty)</em>'}</div>
    </section>
  </div>
</body>
</html>
"""
    (OUTPUTS / f"{report.fixture}.compare.html").write_text(page, encoding="utf-8")


def write_index(reports: list[FixtureReport]) -> None:
    rows = []
    for report in reports:
        html_info = report.outputs["html"]
        rows.append(
            "<tr>"
            f"<td><a href='{report.fixture}.compare.html'>{report.fixture}</a></td>"
            f"<td>{report.next_data_content_chars}</td>"
            f"<td>{report.article_body_dom_text_chars}</td>"
            f"<td>{html_info['chars']}</td>"
            f"<td>{', '.join(html_info['semantic_tags_sample']) or '—'}</td>"
            f"<td>{'yes' if html_info['contains_next_data_prose'] else 'no'}</td>"
            "</tr>"
        )
    index = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>Trafilatura eval index</title>
<style>
body{{font-family:IBM Plex Sans,Segoe UI,sans-serif;margin:1.5rem;background:#f3efe6;color:#1c1a16}}
table{{border-collapse:collapse;background:#fffdf8;width:100%}}
th,td{{border:1px solid #d7d0c2;padding:.5rem .65rem;text-align:left;font-size:.9rem}}
th{{background:#f7f2e8}}
</style></head><body>
<h1>Trafilatura evaluation — side-by-side outputs</h1>
<p>Formats tested: txt, html, xml, markdown. Open a row for input/output comparison.</p>
<table>
<thead><tr>
<th>Fixture</th><th>NEXT content chars</th><th>articleBody DOM</th>
<th>HTML out chars</th><th>Semantic tags in HTML out</th><th>NEXT prose recovered</th>
</tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
</body></html>
"""
    (OUTPUTS / "index.html").write_text(index, encoding="utf-8")


def main() -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    reports: list[FixtureReport] = []
    for name in FIXTURE_URLS:
        path = FIXTURES / name
        if not path.exists():
            raise SystemExit(f"Missing fixture: {path}")
        report = run_fixture(path)
        html = path.read_text(encoding="utf-8", errors="replace")
        write_side_by_side(report, html)
        reports.append(report)

    summary = {
        "library": "trafilatura",
        "formats_tested": list(OUTPUT_FORMATS),
        "notes": [
            "output_format=html|xml preserves semantic tags when body is in the DOM.",
            "Stripe.dev article pages store body in __NEXT_DATA__; articleBody DOM is empty.",
            "R2 raw fixtures were not available in this environment; live fetches used.",
        ],
        "fixtures": [asdict(r) for r in reports],
    }
    (OUTPUTS / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_index(reports)

    print(f"Wrote {len(reports)} fixture reports under {OUTPUTS}")
    for report in reports:
        html_info = report.outputs["html"]
        print(
            f"- {report.fixture}: NEXT={report.next_data_content_chars} "
            f"html_out={html_info['chars']} tags={html_info['semantic_tags_sample']} "
            f"next_recovered={html_info['contains_next_data_prose']}"
        )


if __name__ == "__main__":
    main()
