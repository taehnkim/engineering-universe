import torch

from eng_universe.extraction.contract import FIELDS
from eng_universe.extraction.model import DOMNodeSelector


def test_model_scores_candidates_and_one_missing_option() -> None:
    model = DOMNodeSelector(tag_count=8)
    scores = model(
        torch.tensor([[2, 3, 0]]),
        torch.tensor([[3, 2, 0]]),
        torch.tensor([[3, 2, 0]]),
        torch.tensor([[1, 3, 0]]),
        torch.tensor([[3, 1, 0]]),
        torch.zeros((1, 3, 8), dtype=torch.long),
        torch.zeros((1, 3, 12), dtype=torch.long),
        torch.zeros((1, 3, 49)),
        torch.tensor([[True, True, False]]),
    )
    assert scores.shape == (1, 4, len(FIELDS))
    assert torch.all(scores[0, 2] < -1e30)


def test_profiled_model_scores_match_normal_forward() -> None:
    model = DOMNodeSelector(tag_count=8)
    inputs = (
        torch.tensor([[2, 3]]),
        torch.tensor([[3, 2]]),
        torch.tensor([[3, 2]]),
        torch.tensor([[1, 3]]),
        torch.tensor([[3, 1]]),
        torch.zeros((1, 2, 8), dtype=torch.long),
        torch.zeros((1, 2, 12), dtype=torch.long),
        torch.zeros((1, 2, 49)),
        torch.tensor([[True, True]]),
    )

    regular = model(*inputs)
    profiled, embedding_ms, scoring_ms = model.forward_profiled(*inputs)

    torch.testing.assert_close(profiled, regular)
    assert embedding_ms >= 0
    assert scoring_ms >= 0
