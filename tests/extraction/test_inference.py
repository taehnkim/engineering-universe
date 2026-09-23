from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.inference import DOMExtractor


class StubExtractor(DOMExtractor):
    def __init__(self) -> None:
        pass

    def extract_all(self, html: str) -> dict[str, dict[str, str | int] | None]:
        return {
            "article": {
                "node_id": 1,
                "html": "<article>Body</article>",
                "text": "Body",
            },
            "title": {"node_id": 2, "html": "<h1>Title</h1>", "text": "Title"},
            "authors": {
                "node_id": 3,
                "html": "<p>Ada and Grace</p>",
                "text": "Ada and Grace",
            },
            "date": None,
            "summary": {"node_id": 4, "html": "<p>Deck</p>", "text": "Deck"},
            "relative_date": {
                "node_id": 5,
                "html": "<time>2 days ago</time>",
                "text": "2 days ago",
            },
        }


def test_extract_document_returns_text_and_resolves_relative_date() -> None:
    document = StubExtractor().extract_document("<html></html>", "2026-09-19T12:00:00Z")

    assert document.authors == "Ada and Grace"
    assert document.summary == "Deck"
    assert document.relative_date == "2 days ago"
    assert document.scraped_at == "2026-09-19T12:00:00Z"
    assert document.published_at == "2026-09-17T12:00:00Z"


def test_profiled_prediction_matches_normal_prediction() -> None:
    html = "<html><body><article><h1>Title</h1><p>By: Ada</p><p>Body</p></article></body></html>"
    page = parse_html(html, strip_chrome=True)
    extractor = DOMExtractor()

    normal = extractor.predict_page(page)
    profiled, timings = extractor.predict_page_profiled(page)

    assert profiled == normal
    assert [stage["step"] for stage in timings[:5]] == [
        "Feature extraction",
        "Tensor setup",
        "Embeddings",
        "Neural scoring",
        "Select nodes",
    ]
    assert all(stage["ms"] >= 0 for stage in timings)
