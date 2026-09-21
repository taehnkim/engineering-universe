from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

from eng_universe.extraction.contract import (
    Annotation,
    Field,
    load_annotation,
    save_annotation,
)
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

CORE_RUN_MODULE_PATH = (
    Path(__file__).parents[2] / "scripts/label_extraction_with_jev.py"
)
CORE_RUN_SPEC = importlib.util.spec_from_file_location(
    "core_jev_labeler_run", CORE_RUN_MODULE_PATH
)
assert CORE_RUN_SPEC and CORE_RUN_SPEC.loader
core_jev_labeler_run = importlib.util.module_from_spec(CORE_RUN_SPEC)
CORE_RUN_SPEC.loader.exec_module(core_jev_labeler_run)


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


def test_prepare_html_compacts_large_documents_below_api_limit() -> None:
    paragraphs = "".join(
        f"<div><span>Paragraph {index} {'word ' * 80}</span></div>"
        for index in range(1_000)
    )
    html = f"<html><body><main><h1>Title</h1>{paragraphs}</main></body></html>"

    prepared = jev_labeler.prepare_html(
        html,
        max_candidates=20,
        max_state_chars=20_000,
    )

    assert len(prepared.html) <= 20_000
    assert "Title" in prepared.html
    assert "data-jev-node-id" in prepared.html


def test_question_choices_include_candidates_and_missing() -> None:
    questions = jev_labeler.build_questions([4, 8])

    assert set(questions) == {
        "article",
        "title",
        "authors",
        "date",
        "summary",
        "relative_date",
    }
    for question in questions.values():
        assert set(question.criteria) == {"node_4", "node_8", "missing"}


def test_cli_uses_pinned_jev_model() -> None:
    assert jev_labeler_run.build_parser().parse_args([]).model == "jev-1.13.0"


def test_core_cli_defaults_to_bounded_concurrency() -> None:
    args = core_jev_labeler_run.build_parser().parse_args([])

    assert args.concurrency == 5
    assert args.max_retries == 5
    assert not args.include_listings


def test_core_selection_can_include_listing_negatives() -> None:
    records = [
        record("train-article", "train"),
        record("train-listing", "train", is_article=False),
        record("test-article", "test"),
    ]

    articles = core_jev_labeler_run._select_records(
        records,
        splits={"train", "test"},
        page_ids=[],
        limit=0,
    )
    all_pages = core_jev_labeler_run._select_records(
        records,
        splits={"train", "test"},
        page_ids=[],
        limit=0,
        include_listings=True,
    )

    assert [item.page_id for item in articles] == ["train-article", "test-article"]
    assert {item.page_id for item in all_pages} == {
        "train-article",
        "train-listing",
        "test-article",
    }


def test_labeler_bot_supports_shared_async_client() -> None:
    prepared = jev_labeler.prepare_html(
        "<html><body><article><h1>Title</h1><p>Body copy.</p></article></body></html>"
    )

    class FakeUsage:
        def model_dump(self) -> dict[str, int]:
            return {"input_tokens": 100, "output_tokens": 20}

    class FakeClient:
        calls = 0

        async def system_one(self, **kwargs: object) -> SimpleNamespace:
            self.calls += 1
            assert "prepared_html" in kwargs["state"]  # type: ignore[operator]
            answer = SimpleNamespace(
                choice="missing",
                confidence=0.9,
                probabilities={"missing": 0.9},
            )
            return SimpleNamespace(
                answers={field.value: answer for field in Field},
                model="jev-1.13.0",
                usage=FakeUsage(),
            )

    client = FakeClient()
    result = asyncio.run(
        jev_labeler.LabelerBot().label_prepared_async(
            prepared,
            record("page-1", "train"),
            client=client,  # type: ignore[arg-type]
        )
    )

    assert client.calls == 1
    assert result["labels"] == {field.value: None for field in Field}
    assert result["metadata"]["article"]["confidence"] == 0.9


def test_labeler_bot_can_rerun_one_field() -> None:
    prepared = jev_labeler.prepare_html(
        "<html><body><article><h1>Title</h1><p>Body copy.</p></article></body></html>"
    )

    class FakeUsage:
        def model_dump(self) -> dict[str, int]:
            return {"input_tokens": 50, "output_tokens": 5}

    class FakeClient:
        questions: set[str] = set()

        async def system_one(self, **kwargs: object) -> SimpleNamespace:
            self.questions = set(kwargs["questions"])  # type: ignore[arg-type]
            answer = SimpleNamespace(
                choice="missing",
                confidence=0.82,
                probabilities={"missing": 0.82},
            )
            return SimpleNamespace(
                answers={"authors": answer},
                model="jev-1.13.0",
                usage=FakeUsage(),
            )

    client = FakeClient()
    result = asyncio.run(
        jev_labeler.LabelerBot().label_field_prepared_async(
            prepared,
            record("page-1", "train"),
            Field.AUTHORS,
            client=client,  # type: ignore[arg-type]
        )
    )

    assert client.questions == {"authors"}
    assert result["field"] == "authors"
    assert result["node_id"] is None
    assert result["metadata"]["confidence"] == 0.82
    assert result["metadata"]["latency_ms"] >= 0


def test_labeler_bot_seeds_draft_and_preserves_reviewed_annotation(
    tmp_path: Path,
) -> None:
    bot = jev_labeler.LabelerBot()
    result = {
        "page_id": "page-1",
        "html_hash": "hash",
        "labels": {field.value: index for index, field in enumerate(Field)},
    }

    assert bot.seed_core_annotation(result, tmp_path)
    seeded = load_annotation(tmp_path / "annotations/page-1.json")
    assert seeded.review_status == "draft"
    assert seeded.needs_review
    assert seeded.labels[Field.AUTHORS] == 2

    reviewed = Annotation(
        page_id="page-1",
        html_hash="hash",
        labels={field: None for field in Field},
        review_status="reviewed",
    )
    save_annotation(tmp_path / "annotations/page-1.json", reviewed)

    assert not bot.seed_core_annotation(result, tmp_path)
    assert load_annotation(tmp_path / "annotations/page-1.json") == reviewed
