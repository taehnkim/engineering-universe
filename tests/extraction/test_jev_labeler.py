from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from eng_universe.extraction.manifest import PageRecord


MODULE_PATH = Path(__file__).parents[2] / "labeler-bot/jev_labeler.py"
SPEC = importlib.util.spec_from_file_location("jev_labeler", MODULE_PATH)
assert SPEC and SPEC.loader
jev_labeler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = jev_labeler
SPEC.loader.exec_module(jev_labeler)

RUN_MODULE_PATH = Path(__file__).parents[2] / "labeler-bot/run.py"
RUN_SPEC = importlib.util.spec_from_file_location("jev_labeler_run", RUN_MODULE_PATH)
assert RUN_SPEC and RUN_SPEC.loader
jev_labeler_run = importlib.util.module_from_spec(RUN_SPEC)
RUN_SPEC.loader.exec_module(jev_labeler_run)


def record(page_id: str, split: str, *, is_article: bool = True) -> PageRecord:
    return PageRecord(
        page_id=page_id,
        source_id="source",
        company="Company",
        website=f"{split}.example",
        url=f"https://example.com/{page_id}",
        html_path=f"html/{page_id}.html",
        split=split,  # type: ignore[arg-type]
        capture_kind="browser",
        html_hash="hash",
        is_article=is_article,
    )


def test_select_records_mixes_splits_and_skips_listings() -> None:
    records = [
        *(record(f"train-{index}", "train") for index in range(5)),
        *(record(f"validation-{index}", "validation") for index in range(5)),
        *(record(f"test-{index}", "test") for index in range(5)),
        record("listing", "train", is_article=False),
    ]

    selected = jev_labeler.select_records(records, 10)

    assert [item.split for item in selected] == [
        "train",
        "validation",
        "test",
        "train",
        "validation",
        "test",
        "train",
        "validation",
        "test",
        "train",
    ]
    assert all(item.page_id != "listing" for item in selected)


def test_prepare_html_removes_chrome_and_keeps_original_node_ids() -> None:
    html = """
    <html><head><script>bad()</script></head><body>
      <nav>Site menu</nav>
      <main class="post-content">
        <header><h1>Useful title</h1><p class="byline">By Ada</p><time>2026-09-19</time></header>
        <article><p>This is the main article body with enough useful words to identify it.</p></article>
      </main>
      <footer>Subscribe</footer>
    </body></html>
    """
    original = jev_labeler.parse_html(html)
    expected_title = next(
        candidate.node_id for candidate in original.candidates if candidate.element.name == "h1"
    )

    prepared = jev_labeler.prepare_html(html, max_candidates=20)

    assert "bad()" not in prepared.html
    assert "Site menu" not in prepared.html
    assert "Subscribe" not in prepared.html
    assert f'data-jev-node-id="{expected_title}"' in prepared.html
    assert expected_title in prepared.candidate_ids
    assert prepared.page.html_hash == original.html_hash
    assert len(prepared.candidate_ids) <= 20


def test_question_choices_include_candidates_and_missing() -> None:
    questions = jev_labeler.build_questions([4, 8])

    assert set(questions) == {"article", "title", "author", "date"}
    for question in questions.values():
        assert set(question.criteria) == {"node_4", "node_8", "missing"}


def test_cli_uses_pinned_jev_model() -> None:
    assert jev_labeler_run.build_parser().parse_args([]).model == "jev-1.13.0"
