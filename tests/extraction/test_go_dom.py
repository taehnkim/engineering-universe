"""Compatibility checks for the opt-in Go DOM adapter."""

from __future__ import annotations

import os

import pytest

from eng_universe.extraction.dom import annotation_html, parse_html


def test_go_backend_reconstructs_original_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><nav>Menu</nav><main><h1>Title</h1></main></body></html>"
    python_page = parse_html(html, strip_chrome=True)
    clean_with_ids = annotation_html(python_page).replace(
        "data-eu-node-id", "data-eu-original-node-id"
    )
    ids = [candidate.node_id for candidate in python_page.candidates]

    def fake_prepare(_html: str) -> dict[str, object]:
        assert _html == html
        return {
            "clean_html": clean_with_ids,
            "preview_html": annotation_html(python_page),
            "node_ids": ids,
        }

    monkeypatch.setattr("eng_universe.extraction.go_dom.prepare_html", fake_prepare)
    go_page = parse_html(html, strip_chrome=True, backend="go")

    assert [candidate.node_id for candidate in go_page.candidates] == ids
    assert go_page.candidate(ids[-1]).get_text(strip=True) == "Title"
    assert annotation_html(go_page) == annotation_html(python_page)


def test_go_backend_rejects_changed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "eng_universe.extraction.go_dom.prepare_html",
        lambda _html: {
            "clean_html": '<main data-eu-original-node-id="5">Body</main>',
            "preview_html": '<main data-eu-node-id="5">Body</main>',
            "node_ids": [6],
        },
    )
    with pytest.raises(ValueError, match="candidate IDs changed"):
        parse_html("<main>Body</main>", strip_chrome=True, backend="go")


def test_go_backend_requires_cleanup() -> None:
    with pytest.raises(ValueError, match="requires strip_chrome"):
        parse_html("<main>Body</main>", backend="go")


@pytest.mark.skipif(
    not os.environ.get("ENG_UNIVERSE_GO_DOM_LIBRARY"),
    reason="Go shared library is not configured",
)
def test_go_shared_library_simple_page() -> None:
    html = "<html><body><nav>Menu</nav><main><h1>Title</h1><p>Body</p></main></body></html>"
    python_page = parse_html(html, strip_chrome=True)
    go_page = parse_html(html, strip_chrome=True, backend="go")
    assert [candidate.node_id for candidate in go_page.candidates] == [
        candidate.node_id for candidate in python_page.candidates
    ]
    assert "Menu" not in annotation_html(go_page)
