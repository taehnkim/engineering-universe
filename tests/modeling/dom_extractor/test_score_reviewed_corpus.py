from pathlib import Path
from types import SimpleNamespace

from eng_universe.extraction.contract import FIELDS, Field
from modeling.dom_extractor.commands import score_reviewed_corpus


def test_score_reports_exact_and_present_only_accuracy(
    monkeypatch, tmp_path: Path
) -> None:
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"checkpoint")
    pages = [
        SimpleNamespace(
            record=SimpleNamespace(website="one.test"),
            annotation=SimpleNamespace(labels={field: None for field in FIELDS}),
            page=SimpleNamespace(original_html="first"),
        ),
        SimpleNamespace(
            record=SimpleNamespace(website="two.test"),
            annotation=SimpleNamespace(
                labels={field: (3 if field is Field.AUTHORS else None) for field in FIELDS}
            ),
            page=SimpleNamespace(original_html="second"),
        ),
    ]

    class FakeExtractor:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def predict_ids(self, html: str) -> dict[Field, int | None]:
            if html == "second":
                return {field: (2 if field is Field.AUTHORS else None) for field in FIELDS}
            return {field: None for field in FIELDS}

    monkeypatch.setattr(score_reviewed_corpus, "DOMExtractor", FakeExtractor)
    monkeypatch.setattr(
        score_reviewed_corpus, "iter_labeled_pages", lambda _dataset, _split: pages
    )

    report = score_reviewed_corpus.score(tmp_path, "validation", checkpoint)

    assert report["pages"] == 2
    assert report["websites"] == 2
    assert report["fields"]["authors"] == {
        "correct": 1,
        "total": 2,
        "exact_accuracy": 0.5,
        "present_correct": 0,
        "present": 1,
        "present_accuracy": 0.0,
    }
    assert report["fields"]["title"]["exact_accuracy"] == 1.0
