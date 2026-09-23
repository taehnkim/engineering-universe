from __future__ import annotations

from modeling.dom_extractor.commands.compare_checkpoints import gate


def _row(reference: int, candidate: int) -> dict[str, int]:
    return {
        "reference_exact": reference,
        "candidate_exact": candidate,
        "reference_present_exact": reference,
        "candidate_present_exact": candidate,
    }


def test_gate_accepts_held_out_gain_without_site_regression() -> None:
    report = {
        "by_split": {
            "validation": {"authors": _row(7, 8)},
            "test": {"authors": _row(5, 6)},
        },
        "by_site": {
            "validation": {"site-a": {"authors": _row(7, 8)}},
            "test": {"site-b": {"authors": _row(5, 6)}},
        },
    }

    assert gate(report) == []


def test_gate_rejects_field_regression_hidden_by_pooled_author_gain() -> None:
    report = {
        "by_split": {
            "validation": {"authors": _row(7, 8)},
            "test": {"authors": _row(5, 6)},
        },
        "by_site": {
            "validation": {
                "site-a": {
                    "authors": _row(7, 8),
                    "title": _row(9, 8),
                }
            },
            "test": {"site-b": {"authors": _row(5, 6)}},
        },
    }

    assert "validation/site-a/title exact-node regression" in gate(report)


def test_gate_requires_present_author_and_test_gain() -> None:
    report = {
        "by_split": {
            "validation": {"authors": _row(7, 7)},
            "test": {"authors": _row(5, 5)},
        },
        "by_site": {
            "validation": {"site-a": {"authors": _row(7, 7)}},
            "test": {"site-b": {"authors": _row(5, 5)}},
        },
    }

    assert len(gate(report)) == 3
