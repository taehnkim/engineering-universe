import torch

from eng_universe.extraction.model import DOMNodeSelector
from eng_universe.extraction.training import MatrixPage, collate_pages


def test_model_scores_candidates_and_one_missing_option() -> None:
    model = DOMNodeSelector(tag_count=8)
    scores = model(
        torch.tensor([[2, 3, 0]]),
        torch.tensor([[3, 2, 0]]),
        torch.zeros((1, 3, 7)),
        torch.tensor([[True, True, False]]),
    )
    assert scores.shape == (1, 4, 4)
    assert torch.all(scores[0, 2] < -1e30)


def test_collate_rewrites_per_page_missing_target_after_padding() -> None:
    one = MatrixPage(
        "one",
        "one.test",
        torch.tensor([2]),
        torch.tensor([3]),
        torch.zeros((1, 7)),
        torch.tensor([1, 0, 1, 1]),
    )
    two = MatrixPage(
        "two",
        "two.test",
        torch.tensor([2, 3]),
        torch.tensor([3, 2]),
        torch.zeros((2, 7)),
        torch.tensor([2, 1, 2, 0]),
    )
    batch = collate_pages([one, two])
    assert batch.targets.tolist() == [[2, 0, 2, 2], [2, 1, 2, 0]]
    assert batch.mask.tolist() == [[True, False], [True, True]]
