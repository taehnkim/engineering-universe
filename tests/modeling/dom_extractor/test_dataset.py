from eng_universe.extraction.contract import FIELDS, Annotation, Field
from modeling.dom_extractor.dataset import LabeledPage, prepare_page
from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import (
    FeatureNormalizer,
    TagVocabulary,
    featurize_page,
)
from modeling.dom_extractor.manifest import PageRecord


def test_prepared_targets_map_stable_node_ids_to_clean_candidate_indices() -> None:
    html = "<nav>Menu</nav><main><h1>Title</h1><p>Body</p></main>"
    page = parse_html(html, strip_chrome=True)
    title = next(item for item in page.candidates if item.element.name == "h1")
    labels = {field: None for field in FIELDS}
    labels[Field.TITLE] = title.node_id
    item = LabeledPage(
        record=PageRecord(
            page_id="page",
            source_id="source",
            company="Company",
            website="example.com",
            url="https://example.com/page",
            html_path="html/page.html",
            split="train",
            capture_kind="browser",
            html_hash=page.html_hash,
        ),
        page=page,
        annotation=Annotation("page", page.html_hash, labels),
    )
    vocabulary = TagVocabulary.fit([page])
    normalizer = FeatureNormalizer.fit([featurize_page(page, vocabulary).numeric])

    prepared = prepare_page(item, vocabulary, normalizer)

    expected_index = list(prepared.features.node_ids).index(title.node_id)
    assert prepared.targets[list(FIELDS).index(Field.TITLE)] == expected_index
