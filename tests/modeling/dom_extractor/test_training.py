import torch

from modeling.dom_extractor.training import MatrixPage, collate_pages


def test_collate_rewrites_per_page_missing_target_after_padding() -> None:
    one = MatrixPage(
        "one",
        "one.test",
        torch.tensor([2]),
        torch.tensor([3]),
        torch.tensor([3]),
        torch.tensor([1]),
        torch.tensor([1]),
        torch.zeros((1, 8), dtype=torch.long),
        torch.zeros((1, 12), dtype=torch.long),
        torch.zeros((1, 49)),
        torch.tensor([1, 0, 1, 1, 1, 1]),
    )
    two = MatrixPage(
        "two",
        "two.test",
        torch.tensor([2, 3]),
        torch.tensor([3, 2]),
        torch.tensor([3, 2]),
        torch.tensor([1, 2]),
        torch.tensor([3, 1]),
        torch.zeros((2, 8), dtype=torch.long),
        torch.zeros((2, 12), dtype=torch.long),
        torch.zeros((2, 49)),
        torch.tensor([2, 1, 2, 0, 2, 2]),
    )

    batch = collate_pages([one, two])

    assert batch.targets.tolist() == [
        [2, 0, 2, 2, 2, 2],
        [2, 1, 2, 0, 2, 2],
    ]
    assert batch.mask.tolist() == [[True, False], [True, True]]
