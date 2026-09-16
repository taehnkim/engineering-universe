from __future__ import annotations

from eng_universe.ingest.etl import parse_html

STRIPE_SHELL = """<!DOCTYPE html>
<html>
<head>
  <title>Doing more with less | Stripe</title>
  <meta property="og:title" content="Doing more with less: Reducing requests to the Stripe API"/>
  <meta property="og:url" content="https://stripe.dev/blog/doing-more-with-less-reducing-requests-to-the-stripe-api"/>
  <meta name="author" content="Cecil Phillip"/>
</head>
<body>
  <main>
    <h1>Doing more with less</h1>
    <div class="BlogPost-module__x__articleBody"></div>
    <aside>About the author Cecil Phillip is a part of the Developer Relations team at Stripe.</aside>
  </main>
  <script id="__NEXT_DATA__" type="application/json">
  {"props":{"pageProps":{"postData":{"title":"Doing more with less","content":"\\nIt is important for developers to optimize third-party API usage to reduce costs.\\n\\n## Expand\\n\\nUse the [Expand](https://docs.stripe.com/expand) feature to fetch related objects in one request.\\n\\n```\\nconst charge = await stripe.charges.retrieve('ch_123', {expand: ['customer']});\\n```\\n"}}}}
  </script>
</body>
</html>
"""


def test_parse_html_uses_next_data_when_article_body_empty() -> None:
    url = "https://stripe.dev/blog/doing-more-with-less-reducing-requests-to-the-stripe-api"
    parsed = parse_html(url, STRIPE_SHELL)
    assert "optimize third-party API usage" in parsed.content
    assert "Expand" in parsed.content
    assert "About the author Cecil Phillip is a part of the Developer Relations" not in parsed.content
    assert parsed.title.startswith("Doing more with less")


def test_parse_html_keeps_dom_when_body_present() -> None:
    html = """<!DOCTYPE html>
<html><head><title>Hello</title>
<meta property="og:title" content="Hello World"/>
</head>
<body><main><article>
<p>This is the real article body with enough words to pass thresholds for a normal DOM post.</p>
<p>Second paragraph keeps the extract grounded in visible HTML.</p>
</article></main></body></html>
"""
    parsed = parse_html("https://example.com/blog/hello", html)
    assert "real article body" in parsed.content
    assert "Second paragraph" in parsed.content


def test_parse_html_json_ld_article_body() -> None:
    html = """<!DOCTYPE html>
<html><head><title>LD</title>
<script type="application/ld+json">
{"@type":"BlogPosting","headline":"LD Post","articleBody":"JSON-LD article body text that is long enough to count as the primary post content for extraction tests when the visible DOM only has short navigation chrome leftover."}
</script>
</head>
<body><main><p>Short chrome</p></main></body></html>
"""
    parsed = parse_html("https://example.com/ld", html)
    assert "JSON-LD article body text" in parsed.content
