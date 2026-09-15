# How seeding works

## Short answer

The system does **not** guess company blogs.

1. A human adds a **source** (company + host + listing root + article path rules).
2. `seed` enqueues that listing root (example: `https://stripe.dev/blog`).
3. The crawler fetches the listing, follows only URLs that match the rules, and stores only **article** URLs.
4. Depth is a safety limit. The path rules decide what is a post.

A single post URL is allowed as a one-off seed only when it already matches a known article pattern.

---

## Your examples

| You give | System action |
|----------|----------------|
| `https://stripe.dev/blog` | Listing root. Follow links. Do not store the listing as a document. |
| `https://medium.com/airbnb-engineering` | Listing root for the Airbnb Medium publication. Same as above. |
| `https://medium.com/airbnb-engineering/project-lighthouse-...` | Article. Store it. Do not treat Medium home or unrelated pubs as in-scope. |
| `https://stripe.com/jobs` | Reject. Not in listing or article rules. |

---

## How do we know which company URLs to crawl?

**Curated catalog:** `eng_universe/ingest/sources.py` → `SOURCES`.

Each `SourceConfig` has:

- `host` — allowed hostname (example: `stripe.dev`)
- `seed_urls` — listing roots you seed with
- `listing_paths` — index/hub paths (follow, do not store)
- `follow_path_patterns` — extra hubs such as `/blog/page/2`
- `article_path_patterns` — post path regexes (store)
- `sitemap_paths` — optional sitemaps to enqueue with a listing seed

There is no open-web discovery of "engineering blogs" yet. Finding Stripe is a human step. Encoding `https://stripe.dev/blog` in the catalog is the machine step.

`SEED_START_URLS` can override which roots run for one job. `python main.py seed --catalog` seeds every root in `SOURCES`.

---

## How do we ignore non-blog URLs?

`classify_url()` returns one of:

- `listing` — crawl for links only
- `article` — crawl and store
- `sitemap` — parse `<loc>` entries through the same classifier
- `reject` — drop

Same-host marketing pages, careers pages, and other pubs fail the path rules and become `reject`.

Shared hosts (Medium) need a **publication prefix** in the rules:

```text
host: medium.com
listing: /airbnb-engineering
article: ^/airbnb-engineering/[slug]$
```

That keeps Airbnb posts and drops `medium.com/@someone` and other publications.

---

## How deep from the initial URL?

```text
depth 0  seed listing (or one article / sitemap)
   |
   +--> depth 1  links that pass classify_url
   |
   +--> depth 2  ...
   |
   stop when depth >= CRAWL_DEPTH_LIMIT (default 3)
```

Depth alone does not mark a page as a post. Only `article_path_patterns` do.

Typical blog:

- depth 0 = `/blog` (listing)
- depth 1 = `/blog/my-post` (article)

Pagination uses `follow_path_patterns` so `/blog/page/2` stays a listing and can discover more articles within the depth budget.

---

## Recommended operator flow

```text
"I found Stripe. Root is https://stripe.dev/blog"
        |
        v
Add SourceConfig (host, listing_paths, article_path_patterns)
        |
        v
python main.py seed --url https://stripe.dev/blog
   or
python main.py seed --catalog
        |
        v
python main.py crawl
```

Classify before you seed:

```bash
uv run python scripts/seed_urls.py --classify https://stripe.dev/blog
uv run python scripts/seed_urls.py --classify https://stripe.dev/blog/some-post
uv run python scripts/seed_urls.py --classify https://stripe.com/jobs
```

---

## What this system does not do (yet)

- Auto-find company blogs from a company name alone
- ML "is this a blog post?" classifiers
- Blind crawl of an entire domain without path rules

Those stay out of scope until a discovery stage exists. Until then, the catalog is the source of truth.
