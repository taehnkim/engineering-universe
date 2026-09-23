from eng_universe.extraction.dom import annotation_html, html_sha256, parse_html

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


def test_chrome_cleanup_removes_common_containers_and_preserves_node_ids() -> None:
    html = """
    <html><body>
      <nav>Menu</nav><div class="cookie-banner">Accept cookies</div>
      <main class="post-content"><h1>Title</h1><p>Article body</p></main>
      <div class="related-posts">Another story</div><footer>Footer</footer>
    </body></html>
    """
    raw = parse_html(html)
    cleaned = parse_html(html, strip_chrome=True)
    raw_title = next(item for item in raw.candidates if item.element.name == "h1")
    cleaned_title = next(
        item for item in cleaned.candidates if item.element.name == "h1"
    )

    assert cleaned_title.node_id == raw_title.node_id
    assert "Article body" in cleaned.dom.get_text(" ", strip=True)
    assert "Menu" not in cleaned.dom.get_text(" ", strip=True)
    assert "Accept cookies" not in cleaned.dom.get_text(" ", strip=True)
    assert "Another story" not in cleaned.dom.get_text(" ", strip=True)
    assert "Footer" not in cleaned.dom.get_text(" ", strip=True)


def test_chrome_v2_removes_media_semantic_chrome_and_hidden_nodes() -> None:
    html = """
    <html><body>
      <main><h1>Title</h1><p>Article body</p>
        <img alt="hero"><object>object</object><embed><track>
      </main>
      <div class="SiteFooter">Camel footer</div>
      <div id="site_footer">Snake footer</div>
      <div class="site-footer">Kebab footer</div>
      <div class="popup-overlay">Popup</div>
      <div role="alertdialog">Alert dialog</div>
      <div aria-modal="true">Modal</div>
      <div hidden>Hidden attribute</div>
      <div aria-hidden="true">Aria hidden</div>
      <div style="display: none !important">Style hidden</div>
      <div><span></span></div>
    </body></html>
    """

    cleaned = parse_html(html, strip_chrome=True)
    text = cleaned.dom.get_text(" ", strip=True)

    assert text == "Title Article body"
    assert not cleaned.dom.find_all(["img", "object", "embed", "track"])
    assert all(
        element.get_text(strip=True) or element.find(True)
        for element in cleaned.dom.find_all(["div", "span"])
    )


def test_annotation_html_uses_cleaned_dom_with_original_node_ids() -> None:
    html = """
    <html><body><nav>Menu</nav><main><h1>Title</h1><img alt="hero"></main></body></html>
    """
    raw = parse_html(html)
    raw_title_id = next(
        candidate.node_id
        for candidate in raw.candidates
        if candidate.element.name == "h1"
    )

    rendered = annotation_html(raw)

    assert "Menu" not in rendered
    assert "<img" not in rendered
    assert f'data-eu-node-id="{raw_title_id}"' in rendered


def test_annotation_html_reuses_cleaned_dom_without_mutating_it() -> None:
    html = "<html><body><nav>Menu</nav><main><h1>Title</h1><p>Body</p></main></body></html>"
    raw = parse_html(html)
    cleaned = parse_html(html, strip_chrome=True)
    before = str(cleaned.dom)

    assert annotation_html(cleaned) == annotation_html(raw)
    assert str(cleaned.dom) == before
