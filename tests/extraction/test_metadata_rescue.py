"""Metadata may rescue author selection without becoming the extracted value."""

from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.metadata_rescue import (
    metadata_author_names,
    rescue_author_node,
)


def _node(page, tag: str, text: str) -> int:
    return next(
        candidate.node_id for candidate in page.candidates
        if candidate.element.name == tag
        and candidate.element.get_text(" ", strip=True) == text
    )


def test_jsonld_multiple_authors_rescues_complete_visible_byline() -> None:
    html = """<html><head><script type="application/ld+json">
    {"@type":"Article","author":[{"@type":"Person","name":"Ada Lovelace"},
    {"@type":"Person","name":"Grace Hopper"}]}</script></head>
    <body><article><section class="author-container">
    <div>Ada Lovelace</div><div>Grace Hopper</div>
    </section><p>Body text.</p></article></body></html>"""
    page = parse_html(html, strip_chrome=True)
    partial = _node(page, "div", "Ada Lovelace")
    complete = _node(page, "section", "Ada Lovelace Grace Hopper")

    assert metadata_author_names(html) == ("Ada Lovelace", "Grace Hopper")
    assert rescue_author_node(page, partial) == complete
    assert rescue_author_node(page, complete) == complete


def test_meta_author_finds_tightest_child_even_when_model_says_missing() -> None:
    html = """<html><head><meta name="author" content="Davy Costa"></head>
    <body><article><p><span><a href="/author/davy-costa">Davy Costa</a></span></p>
    </article></body></html>"""
    page = parse_html(html, strip_chrome=True)
    link = _node(page, "a", "Davy Costa")

    assert rescue_author_node(page, None) == link


def test_organization_and_unmatched_metadata_leave_prediction_unchanged() -> None:
    html = """<html><head><meta name="author" content="Thinking Machines Lab"></head>
    <body><article><p>By Ada Lovelace</p></article></body></html>"""
    page = parse_html(html, strip_chrome=True)
    selected = _node(page, "p", "By Ada Lovelace")

    assert metadata_author_names(html) == ()
    assert rescue_author_node(page, selected) == selected


def test_no_matching_visible_name_does_not_invent_a_node() -> None:
    html = """<html><head><meta name="author" content="Grace Hopper"></head>
    <body><article><p>By Ada Lovelace</p></article></body></html>"""
    page = parse_html(html, strip_chrome=True)
    selected = _node(page, "p", "By Ada Lovelace")

    assert rescue_author_node(page, selected) == selected
