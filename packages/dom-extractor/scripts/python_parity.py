"""Development-only reference output for comparing the Node port to Python."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import featurize_page
from eng_universe.extraction.inference import DOMExtractor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html_path", type=Path)
    parser.add_argument("--backend", choices=("python", "go"), default="go")
    args = parser.parse_args()
    html = args.html_path.read_text(encoding="utf-8")
    model = DOMExtractor(dom_backend=args.backend)
    page = parse_html(html, strip_chrome=True, backend=args.backend)
    features = featurize_page(
        page, model.vocabulary, model.semantic_vocabulary, model.normalizer
    )
    values = {
        "nodeIds": features.node_ids.tolist(),
        "tagIds": features.tag_ids.tolist(),
        "parentTagIds": features.parent_tag_ids.tolist(),
        "grandparentTagIds": features.grandparent_tag_ids.tolist(),
        "previousTagIds": features.previous_tag_ids.tolist(),
        "nextTagIds": features.next_tag_ids.tolist(),
        "attributeTokenIds": features.attribute_token_ids.tolist(),
        "textShapeTokenIds": features.text_shape_token_ids.tolist(),
        "numeric": features.numeric.tolist(),
        "predictions": {field.value: value for field, value in model.predict_page(page).items()},
    }
    print(json.dumps(values, separators=(",", ":")))


if __name__ == "__main__":
    main()
