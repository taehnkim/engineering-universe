from __future__ import annotations

import unittest

from eng_universe.ingest.etl import parse_html


class ParseHtmlTests(unittest.TestCase):
    def test_prefers_main_and_strips_chrome(self) -> None:
        html = """
        <html>
          <head>
            <title>Page Title</title>
            <meta property="og:title" content="Article Title" />
            <meta name="author" content="Ada Lovelace" />
            <meta property="article:published_time" content="2024-01-02T03:04:05Z" />
            <link rel="canonical" href="https://example.com/blog/post" />
          </head>
          <body>
            <nav>Home Blog</nav>
            <article><p>Teaser card outside main.</p></article>
            <main>
              <h1>Hello</h1>
              <p>Body one.</p>
              <aside>Related</aside>
              <p>Body two.</p>
            </main>
            <footer>Copyright</footer>
            <script>ignored()</script>
          </body>
        </html>
        """
        parsed = parse_html("https://blog.example.com/post", html)
        self.assertEqual(parsed.title, "Article Title")
        self.assertEqual(parsed.authors, ["Ada Lovelace"])
        self.assertEqual(parsed.published_at, "2024-01-02T03:04:05Z")
        self.assertEqual(parsed.canonical_url, "https://example.com/blog/post")
        self.assertEqual(parsed.company, "blog.example.com")
        self.assertIn("Hello", parsed.content)
        self.assertIn("Body one.", parsed.content)
        self.assertIn("Body two.", parsed.content)
        self.assertNotIn("Home Blog", parsed.content)
        self.assertNotIn("Copyright", parsed.content)
        self.assertNotIn("ignored()", parsed.content)
        self.assertNotIn("Related", parsed.content)
        self.assertNotIn("Teaser card", parsed.content)

    def test_main_with_two_articles_keeps_full_main_text(self) -> None:
        html = """
        <html><body>
          <main>
            <article><p>Teaser card</p></article>
            <article><p>Primary body</p></article>
          </main>
        </body></html>
        """
        parsed = parse_html("https://example.com/post", html)
        self.assertIn("Teaser card", parsed.content)
        self.assertIn("Primary body", parsed.content)

    def test_falls_back_to_article_then_body(self) -> None:
        html_article = """
        <html><body>
          <article><p>Only article text</p></article>
        </body></html>
        """
        parsed = parse_html("https://example.com/x", html_article)
        self.assertEqual(parsed.content, "Only article text")

        html_body = """
        <html><body><p>Only body text</p></body></html>
        """
        parsed_body = parse_html("https://example.com/y", html_body)
        self.assertEqual(parsed_body.content, "Only body text")


if __name__ == "__main__":
    unittest.main()
