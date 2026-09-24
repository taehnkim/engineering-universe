from unittest.mock import patch

import torch

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.inference import DEFAULT_CHECKPOINT, DOMExtractor


class StubExtractor(DOMExtractor):
    def __init__(self) -> None:
        pass

    def extract_all(self, html: str) -> dict[str, dict[str, str | int] | None]:
        return {
            "article": {
                "node_id": 1,
                "html": "<article>Body</article>",
                "text": "Body",
                "confidence": 0.7,
            },
            "title": {"node_id": 2, "html": "<h1>Title</h1>", "text": "Title", "confidence": 0.8},
            "authors": {
                "node_id": 3,
                "html": "<p>Ada and Grace</p>",
                "text": "Ada and Grace",
                "confidence": 0.9,
            },
            "date": None,
        }


def test_extract_document_returns_html_text_and_confidence() -> None:
    document = StubExtractor().extract_document("<html></html>", "2026-09-19T12:00:00Z")

    assert document.authors_text == "Ada and Grace"
    assert document.authors_html == "<p>Ada and Grace</p>"
    assert document.authors_confidence == 0.9
    assert document.scraped_at == "2026-09-19T12:00:00Z"
    assert document.published_at is None


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


def test_empty_logo_title_falls_back_to_populated_heading_only() -> None:
    page = parse_html(
        "<html><body><header><h1 class='SiteHeader__logo'><svg></svg></h1>"
        "<h1>Help</h1></header><article><h1 class='BlogPost__title'>"
        "Article title</h1><p>By Ada</p></article></body></html>",
        strip_chrome=True,
    )
    indexes = {
        candidate.node_id: index for index, candidate in enumerate(page.candidates)
    }
    logo_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.get("class") == ["SiteHeader__logo"]
    )
    title_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.get("class") == ["BlogPost__title"]
    )
    help_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "h1"
        and candidate.element.get_text(" ", strip=True) == "Help"
    )
    authors_id = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "p"
    )
    scores = torch.full((1, len(page.candidates) + 1, len(FIELDS)), -10.0)
    scores[0, -1, :] = 0.0
    title_index = FIELDS.index(Field.TITLE)
    scores[0, indexes[logo_id], title_index] = 12.0
    scores[0, indexes[title_id], title_index] = 11.0
    scores[0, indexes[help_id], title_index] = 9.0
    scores[0, indexes[authors_id], FIELDS.index(Field.AUTHORS)] = 10.0
    extractor = DOMExtractor(DEFAULT_CHECKPOINT)

    with patch.object(extractor.model, "forward", return_value=scores):
        predicted = extractor.predict_page(page)

    assert predicted[Field.TITLE] == title_id
    assert predicted[Field.AUTHORS] == authors_id
