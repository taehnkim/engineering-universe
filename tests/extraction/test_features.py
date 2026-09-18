import numpy as np

from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import (
    FeatureNormalizer,
    TagVocabulary,
    featurize_page,
    raw_numeric_features,
)


def test_structural_features_and_unknown_tag() -> None:
    page = parse_html(
        "<html><body><article><p>Published January 2, 2025</p>"
        "<p><a href='/x'>linked</a> plain</p><time datetime='2025-01-02'>date</time>"
        "</article></body></html>"
    )
    article = next(item for item in page.candidates if item.element.name == "article")
    vector = raw_numeric_features(article, len(page.candidates))
    assert vector.shape == (7,)
    assert np.isclose(vector[1], np.log1p(2))
    assert 0 < vector[2] < 1
    assert vector[5] == 0
    assert vector[6] == 1
    time = next(item for item in page.candidates if item.element.name == "time")
    assert raw_numeric_features(time, len(page.candidates))[5] == 1

    vocabulary = TagVocabulary.fit([page])
    assert vocabulary.encode("article") > 1
    assert vocabulary.encode("made-up-tag") == 1
    raw = featurize_page(page, vocabulary)
    normalizer = FeatureNormalizer.fit([raw.numeric])
    normalized = featurize_page(page, vocabulary, normalizer)
    assert np.isfinite(normalized.numeric).all()
    assert np.allclose(normalized.numeric[:, :5].mean(axis=0), 0, atol=1e-5)
