from eng_universe.ingest.sources import UrlKind, classify_url, resolve_seed_url


def test_stripe_listing_and_article() -> None:
    assert classify_url("https://stripe.dev/blog") == UrlKind.LISTING
    assert (
        classify_url("https://stripe.dev/blog/introducing-something")
        == UrlKind.ARTICLE
    )
    assert classify_url("https://stripe.dev/docs") == UrlKind.REJECT
    assert classify_url("https://stripe.dev/blog/page/2") == UrlKind.LISTING


def test_medium_airbnb_publication_scope() -> None:
    assert (
        classify_url("https://medium.com/airbnb-engineering") == UrlKind.LISTING
    )
    assert (
        classify_url(
            "https://medium.com/airbnb-engineering/"
            "project-lighthouse-part-3-introducing-project-lighthouse-anonymize-74f8b26653fb"
        )
        == UrlKind.ARTICLE
    )
    # Other Medium publications and account pages stay out of scope.
    assert classify_url("https://medium.com/@someone") == UrlKind.REJECT
    assert classify_url("https://medium.com/netflix-techblog/foo") == UrlKind.REJECT
    assert (
        classify_url("https://medium.com/airbnb-engineering/about") == UrlKind.REJECT
    )


def test_meta_dated_article_pattern() -> None:
    assert classify_url("https://engineering.fb.com/") == UrlKind.LISTING
    assert (
        classify_url(
            "https://engineering.fb.com/2024/01/15/production-engineering/example/"
        )
        == UrlKind.ARTICLE
    )
    assert classify_url("https://engineering.fb.com/careers") == UrlKind.REJECT


def test_resolve_seed_url_rejects_unknown_host() -> None:
    kind, error = resolve_seed_url("https://example.com/blog")
    assert kind == UrlKind.REJECT
    assert error is not None
    assert "curated source catalog" in error


def test_resolve_seed_url_accepts_listing_and_article() -> None:
    kind, error = resolve_seed_url("https://stripe.dev/blog")
    assert kind == UrlKind.LISTING
    assert error is None
    kind, error = resolve_seed_url(
        "https://medium.com/airbnb-engineering/"
        "project-lighthouse-part-3-introducing-project-lighthouse-anonymize-74f8b26653fb"
    )
    assert kind == UrlKind.ARTICLE
    assert error is None


def test_new_multi_seed_sources_and_article_guards() -> None:
    assert classify_url("https://cursor.com/blog/topic/product") == UrlKind.LISTING
    assert classify_url("https://cursor.com/blog/topic/research") == UrlKind.LISTING
    assert (
        classify_url("https://www.perplexity.ai/hub/blog/category/research")
        == UrlKind.LISTING
    )
    assert (
        classify_url("https://www.perplexity.ai/hub/blog/fast-embeddings-on-gpus")
        == UrlKind.ARTICLE
    )
    assert (
        classify_url("https://developer.nvidia.com/blog/category/generative-ai/")
        == UrlKind.LISTING
    )
    assert (
        classify_url("https://developer.nvidia.com/blog/how-to-size-gpus-for-ai/")
        == UrlKind.ARTICLE
    )
    assert classify_url("https://planetscale.com/blog/author/example") == UrlKind.REJECT
    assert (
        classify_url("https://brain.co/blog/deployed-at-brain-co")
        == UrlKind.ARTICLE
    )
