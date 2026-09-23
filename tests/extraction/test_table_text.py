from eng_universe.extraction.dom import parse_html, readable_text


def test_semantic_table_preserves_header_value_pairs() -> None:
    page = parse_html(
        "<article><p>Prices are per million tokens.</p><table>"
        "<thead><tr><th>Model</th><th>Input</th><th>Output</th></tr></thead>"
        "<tbody><tr><td>GPT-6 Sol</td><td>$2</td><td>$10</td></tr></tbody>"
        "</table><p>More details.</p></article>"
    )
    assert readable_text(page.dom.article) == (
        "Prices are per million tokens.\n\n"
        "- GPT-6 Sol\n"
        "  - Input: $2\n"
        "  - Output: $10\n\n"
        "More details."
    )


def test_bold_td_header_rows_are_supported() -> None:
    page = parse_html(
        "<table><tbody>"
        "<tr><td><b>Model</b></td><td><b>Input</b></td></tr>"
        "<tr><td>GPT-6 Sol</td><td>$2</td></tr>"
        "</tbody></table>"
    )
    assert readable_text(page.dom.table) == "- GPT-6 Sol\n  - Input: $2"


def test_div_grid_still_formats_after_empty_corner_is_pruned() -> None:
    page = parse_html(
        "<article><div>"
        '<div class="grid"><div></div><div>Grok 4.7</div><div>Grok 4.6</div></div>'
        '<div class="grid"><div>Input price</div><div>$2</div><div>$2</div></div>'
        '<div class="grid"><div>Output price</div><div>$6</div><div>$6</div></div>'
        "</div></article>",
        strip_chrome=True,
    )
    assert readable_text(page.dom.article) == (
        "- Input price\n"
        "  - Grok 4.7: $2\n"
        "  - Grok 4.6: $2\n"
        "- Output price\n"
        "  - Grok 4.7: $6\n"
        "  - Grok 4.6: $6"
    )


def test_ambiguous_grid_falls_back_without_invented_labels() -> None:
    page = parse_html(
        '<article><div><div class="grid"><div>A</div><div>B</div></div>'
        '<div class="grid"><div>C</div><div>D</div></div>'
        '<div class="grid"><div>E</div><div>F</div></div></div></article>'
    )
    text = readable_text(page.dom.article)
    assert "- A" not in text
    assert all(value in text for value in "ABCDEF")
