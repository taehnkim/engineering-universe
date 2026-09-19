from eng_universe.extraction.evaluation import _empty_counts, _finish


def test_empty_evaluation_has_no_accuracy() -> None:
    result = _finish(_empty_counts())

    assert result["examples"] == 0
    assert result["exact_selected_node_accuracy"] is None
    assert result["missing_precision"] is None
    assert result["missing_recall"] is None
