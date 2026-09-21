# Redis Schema and Queries

When `EMBEDDINGS_PROVIDER=pylate`, Redis still stores document metadata, but vector
search uses a local PLAID index on disk instead of RediSearch.

## Keys

- `eu:v1:q:ready:crawl` sorted set of ready and delayed crawl runs.
- `eu:v1:q:leased:crawl` sorted set of leased crawl runs.
- `eu:v1:q:ready:index_raw` sorted set of crawl documents ready for indexing.
- `eu:v1:q:leased:index_raw` sorted set of leased raw-index runs.
- `eu:v1:run:{run_id}` hash of stage input, state, attempts, and lease data.
- `eu:v1:idem:{digest}` string that maps semantic work to its canonical run.
- `crawl:seen` set of normalized URLs that were enqueued.
- `crawl:doc_seq` integer sequence for crawl doc IDs.
- `crawl:doc:{doc_id}` hash of crawl metadata (url, domain, depth, status, raw_key, clean_key).
- `doc:{doc_id}` hash of indexed document fields.
- `robots:{domain}` hash of robots rules.
- `robots:next_allowed:{domain}` string unix timestamp.

## Object Storage (R2)

When `R2_UPLOAD=true`, crawler raw HTML and indexer clean text/index payloads are
stored in R2 using:

- `raw/{doc_id}.html`
- `clean/{doc_id}.txt` — cleaned body text
- `index/{doc_id}.json` — metadata + artifact keys (no body; use `clean_key`)

## RediSearch Index

When `KEYWORD_ONLY=true`, the schema omits the `embedding` vector field and
keyword search runs without embeddings.

```
FT.CREATE idx:blogs ON HASH PREFIX 1 doc: SCHEMA \
  title TEXT WEIGHT 2 \
  description TEXT WEIGHT 1 \
  subject TEXT WEIGHT 2 NOSTEM \
  catalogNumber TEXT WEIGHT 2 NOSTEM \
  instructor TEXT WEIGHT 1 NOSTEM PHONETIC dm:en \
  component TAG SEPARATOR , \
  level TAG SEPARATOR , \
  genEdArea TAG SEPARATOR , \
  academicYear NUMERIC \
  content TEXT WEIGHT 1 \
  topics TAG SEPARATOR , \
  source TAG SEPARATOR , \
  company TAG SEPARATOR , \
  authors TAG SEPARATOR , \
  published_at TEXT \
  url TEXT \
  lang TAG SEPARATOR , \
  embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE
```

## Query Templates

### Keyword (BM25)

```
FT.SEARCH idx:blogs "@title|description|subject|catalogNumber|instructor|content:redis" LIMIT 0 10
```

### Vector + Keyword (Hybrid)

```
FT.SEARCH idx:blogs "(@title|content:redis)=>[KNN 10 @embedding $vec AS vector_score]" \
  PARAMS 2 vec $vector_bytes \
  SORTBY vector_score \
  RETURN 3 title url vector_score \
  DIALECT 2
```

### Filters

```
@company:{Meta} @topics:{Kafka} @lang:{en_US}
```
