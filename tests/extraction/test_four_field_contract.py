from eng_universe.extraction.contract import FIELDS, Annotation
from eng_universe.extraction.dom import parse_html


def test_old_annotation_round_trips_without_losing_legacy_labels() -> None:
    original = {
        "page_id": "example",
        "html_hash": "hash",
        "labels": {
            "article": 4, "title": 5, "authors": None, "date": 7,
            "summary": 8, "relative_date": None,
        },
        "review_status": "reviewed",
    }
    annotation = Annotation.from_dict(original)

    assert [field.value for field in FIELDS] == ["article", "title", "authors", "date"]
    assert annotation.to_dict()["labels"] == original["labels"]


def test_selected_article_text_preserves_paragraphs_and_inline_punctuation() -> None:
    page = parse_html(
        "<article><h1>Title</h1><p>By: <a>Ada</a>, <a>Grace</a></p>"
        "<p>First paragraph.</p><p>Second <em>paragraph</em>.</p></article>"
    )
    article = next(c.node_id for c in page.candidates if c.element.name == "article")
    content = page.selected_content(article)

    assert content["text"] == (
        "Title\n\nBy: Ada, Grace\n\nFirst paragraph.\n\nSecond paragraph."
    )
    assert "<em>paragraph</em>" in content["html"]
