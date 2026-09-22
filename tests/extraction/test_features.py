import numpy as np

from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import (
    MAX_SEMANTIC_TOKENS,
    NUMERIC_FEATURE_NAMES,
    FeatureNormalizer,
    SemanticVocabulary,
    TagVocabulary,
    featurize_page,
    raw_numeric_features,
    semantic_tokens,
)


def test_structural_features_and_unknown_tag() -> None:
    page = parse_html(
        "<html><body><article><p>Published January 2, 2025</p>"
        "<p><a href='/x'>linked</a> plain</p><time datetime='2025-01-02'>date</time>"
        "</article></body></html>"
    )
    article = next(item for item in page.candidates if item.element.name == "article")
    vector = raw_numeric_features(article, len(page.candidates))
    assert vector.shape == (len(NUMERIC_FEATURE_NAMES),)
    assert np.isclose(vector[1], np.log1p(2))
    assert 0 < vector[2] < 1
    assert vector[5] == 0
    assert vector[6] == 1
    assert vector[7] == 0
    time = next(item for item in page.candidates if item.element.name == "time")
    assert raw_numeric_features(time, len(page.candidates))[5] == 1
    assert len(raw_numeric_features(time, len(page.candidates))) == len(
        NUMERIC_FEATURE_NAMES
    )

    relative = parse_html("<p>5 min read · 2 days ago</p>").candidates[0]
    assert raw_numeric_features(relative, 1)[7] == 1

    vocabulary = TagVocabulary.fit([page])
    semantic_vocabulary = SemanticVocabulary.fit([page])
    assert vocabulary.encode("article") > 1
    assert vocabulary.encode("made-up-tag") == 1
    raw = featurize_page(page, vocabulary, semantic_vocabulary)
    normalizer = FeatureNormalizer.fit([raw.numeric])
    normalized = featurize_page(page, vocabulary, semantic_vocabulary, normalizer)
    assert np.isfinite(normalized.numeric).all()
    assert np.allclose(normalized.numeric[:, :5].mean(axis=0), 0, atol=1e-5)
    assert normalized.semantic_token_ids.shape == (
        len(page.candidates),
        MAX_SEMANTIC_TOKENS,
    )


def test_author_and_date_semantic_signals() -> None:
    page = parse_html(
        "<main><header><h1>Title</h1>"
        "<p class='writtenBy contributor'><a rel='author' href='/profile/ada'>"
        "Written by Ada Lovelace & Anthropic Research</a></p>"
        "<time itemprop='datePublished' datetime='2026-09-22'>"
        "Published September 22, 2026 · 5 min read</time>"
        "</header></main>"
    )
    author = next(
        candidate
        for candidate in page.candidates
        if "writtenBy" in candidate.element.get("class", [])
    )
    date = next(
        candidate for candidate in page.candidates if candidate.element.name == "time"
    )
    author_values = raw_numeric_features(author, len(page.candidates))
    date_values = raw_numeric_features(date, len(page.candidates))
    index = {name: position for position, name in enumerate(NUMERIC_FEATURE_NAMES)}

    assert author_values[index["contains_by_prefix"]] == 1
    assert author_values[index["contains_written_by"]] == 1
    assert author_values[index["contains_author_attribute"]] == 1
    assert author_values[index["contains_profile_link"]] == 1
    assert author_values[index["multiple_name_separator"]] == 1
    assert author_values[index["inside_header"]] == 1
    assert date_values[index["contains_absolute_date"]] == 1
    assert date_values[index["contains_published_marker"]] == 1
    assert date_values[index["contains_updated_marker"]] == 0
    assert date_values[index["tag_is_time"]] == 1
    assert date_values[index["itemprop_date_published"]] == 1
    assert "class:written" in semantic_tokens(author.element)
    assert "rel:author" in semantic_tokens(author.element.find("a"))


def test_author_shapes_support_handles_teams_and_non_latin_names() -> None:
    index = {name: position for position, name in enumerate(NUMERIC_FEATURE_NAMES)}
    pages = [
        parse_html("<p class='author'>@octocat</p>"),
        parse_html("<p class='byline'>The GitHub Engineering Team</p>"),
        parse_html("<p class='contributors'>山田 太郎、佐藤 花子</p>"),
    ]

    vectors = [raw_numeric_features(page.candidates[0], 1) for page in pages]

    assert vectors[0][index["person_name_shape"]] == 1
    assert vectors[1][index["contains_byline_attribute"]] == 1
    assert vectors[2][index["contains_author_attribute"]] == 1
    assert all(vector[index["short_text"]] == 1 for vector in vectors)


def test_date_features_distinguish_published_updated_and_reading_time() -> None:
    index = {name: position for position, name in enumerate(NUMERIC_FEATURE_NAMES)}
    page = parse_html(
        "<div><p>Published July 6, 2024</p><p>Updated July 8, 2024</p>"
        "<p>July 6, 2024 · 5 min read</p><p>2 days ago</p>"
        "<p>5 min read</p></div>"
    )
    paragraphs = [
        raw_numeric_features(candidate, len(page.candidates))
        for candidate in page.candidates
        if candidate.element.name == "p"
    ]

    assert paragraphs[0][index["contains_published_marker"]] == 1
    assert paragraphs[0][index["contains_updated_marker"]] == 0
    assert paragraphs[1][index["contains_updated_marker"]] == 1
    assert paragraphs[2][index["contains_absolute_date"]] == 1
    assert paragraphs[2][index["contains_reading_time"]] == 1
    assert paragraphs[3][index["contains_relative_date"]] == 1
    assert paragraphs[4][index["contains_relative_date"]] == 0
    assert paragraphs[4][index["contains_reading_time"]] == 1
