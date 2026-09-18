from eng_universe.extraction.dom import html_sha256, parse_html


HTML = """<!doctype html><html><head><title>Hidden</title><style>x{}</style></head>
<body><main><h1>Headline</h1><script>bad()</script><p>Hello <a href='/'>world</a></p></main></body></html>"""


def test_candidate_ids_are_deterministic_and_skip_excluded_subtrees() -> None:
    first = parse_html(HTML)
    second = parse_html(HTML)
    assert [(c.node_id, c.element.name) for c in first.candidates] == [
        (0, "html"),
        (1, "body"),
        (2, "main"),
        (3, "h1"),
        (4, "p"),
        (5, "a"),
    ]
    assert [str(c.element) for c in first.candidates] == [
        str(c.element) for c in second.candidates
    ]
    assert first.original_html == HTML
    assert first.html_hash == html_sha256(HTML)


def test_selected_content_returns_wrapper_and_text() -> None:
    result = parse_html(HTML).selected_content(4)
    assert result["node_id"] == 4
    assert result["html"] == '<p>Hello <a href="/">world</a></p>'
    assert result["text"] == "Hello world"
