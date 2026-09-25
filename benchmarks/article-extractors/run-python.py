"""Run one Python extractor on the saved HTML corpus without network requests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent


def trafilatura_fields(html: str, url: str) -> dict[str, str | None]:
    import trafilatura

    output = trafilatura.extract(
        html, url=url, output_format="json", include_comments=False,
        with_metadata=True,
    )
    article = json.loads(output) if output else {}
    return {
        "title": article.get("title") or None,
        "body": article.get("text") or None,
        "date": article.get("date") or None,
        "byline": article.get("author") or None,
    }


def newspaper_fields(html: str, url: str) -> dict[str, str | None]:
    from newspaper import Article

    article = Article(url=url or "https://example.invalid/")
    article.download(input_html=html, ignore_read_more=True)
    article.parse()
    published = article.publish_date
    return {
        "title": article.title or None,
        "body": article.text or None,
        "date": published.isoformat() if published else None,
        "byline": ", ".join(article.authors) if article.authors else None,
    }


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"trafilatura", "newspaper"}:
        raise SystemExit("usage: python run-python.py trafilatura|newspaper")
    name = sys.argv[1]
    run = trafilatura_fields if name == "trafilatura" else newspaper_fields
    corpus = json.loads((HERE / ".local/gold.json").read_text())
    pages = corpus["pages"]
    for page in pages[:10]:
        try:
            run(Path(page["htmlPath"]).read_text(), page["url"])
        except Exception:
            pass  # The measured pass records the failure.
    results = []
    for index, page in enumerate(pages, 1):
        html = Path(page["htmlPath"]).read_text()
        start = perf_counter()
        try:
            fields = run(html, page["url"])
            results.append({
                "pageId": page["pageId"],
                "latencyMs": (perf_counter() - start) * 1000,
                "fields": fields,
            })
        except Exception as error:
            results.append({
                "pageId": page["pageId"],
                "latencyMs": (perf_counter() - start) * 1000,
                "error": f"{type(error).__name__}: {error}",
            })
        if index % 100 == 0 or index == len(pages):
            print(f"{name}: {index}/{len(pages)}", flush=True)
    (HERE / f".local/{name}.json").write_text(
        json.dumps({"name": name, "results": results}, indent=2) + "\n"
    )
    print(json.dumps({
        "name": name,
        "pages": len(results),
        "failures": sum("error" in result for result in results),
    }))


if __name__ == "__main__":
    main()
