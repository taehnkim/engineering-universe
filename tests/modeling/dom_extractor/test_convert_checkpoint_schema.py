import hashlib
from pathlib import Path

import torch

from eng_universe.extraction.model import DOMNodeSelector
from modeling.dom_extractor.commands.convert_checkpoint_schema import convert


def test_projection_preserves_four_trained_heads_and_reties_refiner(tmp_path: Path) -> None:
    source = tmp_path / "old.pt"
    converted = tmp_path / "new.pt"
    old_refiner = tmp_path / "old-refiner.pt"
    new_refiner = tmp_path / "new-refiner.pt"
    state = DOMNodeSelector(8, 49, 16, field_count=6).state_dict()
    torch.save({
        "model_state": state,
        "fields": ["article", "title", "authors", "date", "summary", "relative_date"],
        "preprocessing": {"fields": ["article", "title", "authors", "date", "summary", "relative_date"]},
    }, source)
    torch.save({
        "base_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "model_state": {"weight": torch.tensor([1.0])},
    }, old_refiner)

    convert(source, converted, old_refiner, new_refiner)

    result = torch.load(converted, map_location="cpu", weights_only=False)
    assert result["fields"] == ["article", "title", "authors", "date"]
    assert result["preprocessing"]["fields"] == result["fields"]
    for name, value in state.items():
        projected = result["model_state"][name]
        assert torch.equal(projected, value[:4] if name in {
            "missing_scores", "network.4.weight", "network.4.bias"
        } else value)
    refiner = torch.load(new_refiner, map_location="cpu", weights_only=False)
    assert refiner["base_sha256"] == hashlib.sha256(converted.read_bytes()).hexdigest()
    assert torch.equal(refiner["model_state"]["weight"], torch.tensor([1.0]))
