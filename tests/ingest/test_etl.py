from eng_universe.ingest.etl import parse_html


def test_parse_html_derives_published_at_from_relative_date() -> None:
    document = parse_html(
        "https://example.com/post",
        """
        <html><head><meta name="description" content="A short subtitle"></head>
        <body><main><h1>Title</h1><div class="published-date">5 min read · 2 days ago</div></main></body></html>
        """,
        scraped_at="2026-09-19T12:30:00Z",
    )

    assert document.summary == "A short subtitle"
    assert document.relative_date == "2 days ago"
    assert document.scraped_at == "2026-09-19T12:30:00Z"
    assert document.published_at == "2026-09-17T12:30:00Z"


def test_parse_html_does_not_treat_reading_time_as_publication_date() -> None:
    document = parse_html(
        "https://example.com/post",
        "<html><body><main><h1>Title</h1><time>5 min read</time></main></body></html>",
        scraped_at="2026-09-19T12:30:00Z",
    )

    assert document.relative_date is None
    assert document.published_at is None
