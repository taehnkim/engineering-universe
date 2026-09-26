"""The npm eval adapter maps raw selectors to stable cleaned node IDs."""

from __future__ import annotations

import io
import json
import threading

import pytest

from eng_universe.extraction.dom import parse_html
from modeling.dom_extractor.apps.npm_package import NpmPackageExtractor
from modeling.dom_extractor.apps.playground import _infer_html


class StubProcess:
    def __init__(self, response: dict[str, object]) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(json.dumps(response) + "\n")

    def poll(self) -> None:
        return None


@pytest.mark.parametrize(
    "trailing_html",
    ["", '<div class="later"><h1>Wrong title</h1></div>'],
)
def test_npm_selector_survives_chrome_removal_before_selected_node(
    trailing_html: str,
) -> None:
    html = (
        '<html><body><div class="popup">Sign up</div>'
        '<div class="post"><h1>Correct title</h1></div>'
        f"{trailing_html}</body></html>"
    )
    selector = (
        "html:nth-of-type(1) > body:nth-of-type(1) > "
        "div:nth-of-type(2) > h1:nth-of-type(1)"
    )
    page = parse_html(html, strip_chrome=True)
    expected_id = next(
        candidate.node_id for candidate in page.candidates
        if candidate.element.name == "h1"
    )
    cleaned_matches = page.dom.select(selector)
    assert not cleaned_matches or cleaned_matches[0].get_text(strip=True) == "Wrong title"

    empty = {"text": None, "confidence": 0.0}
    response = {
        "fields": {
            "body": empty,
            "title": {
                "text": "Correct title",
                "confidence": 0.99,
                "source": {"selector": selector},
            },
            "byline": empty,
            "date": empty,
        }
    }
    extractor = NpmPackageExtractor.__new__(NpmPackageExtractor)
    extractor._lock = threading.Lock()
    extractor._process = StubProcess(response)

    result = _infer_html(html, extractor)

    assert result["predictions"]["title"] == expected_id
    assert result["results"]["title"]["text"] == "Correct title"
