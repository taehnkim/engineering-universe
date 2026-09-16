  Key Pattern: eu:v1:q:ready:crawl
  Type: Sorted Set
  Description: Crawl stage runs scored by ready time
  ────────────────────────────────────────
  Key Pattern: eu:v1:q:leased:crawl
  Type: Sorted Set
  Description: Leased crawl runs scored by lease expiry
  ────────────────────────────────────────
  Key Pattern: crawl:seen
  Type: Set
  Description: Dedupe set of all URLs ever enqueued
  ────────────────────────────────────────
  Key Pattern: crawl:doc_seq
  Type: String (int)
  Description: Auto-incrementing counter for doc IDs
  ────────────────────────────────────────
  Key Pattern: crawl:doc:{docId}
  Type: Hash
  Description: Metadata for a crawled page (url, domain, paths, status, etc.)
  ────────────────────────────────────────
  Key Pattern: eu:v1:q:ready:index_raw
  Type: Sorted Set
  Description: Raw-index stage runs scored by ready time
  ────────────────────────────────────────
  Key Pattern: eu:v1:q:leased:index_raw
  Type: Sorted Set
  Description: Leased raw-index runs scored by lease expiry
  ────────────────────────────────────────
  Key Pattern: robots:{domain}
  Type: Hash
  Description: Cached robots.txt rules (crawl_delay, request_rate, allowed, text)
  ────────────────────────────────────────
  Key Pattern: robots:next_allowed:{domain}
  Type: String (int)
  Description: Timestamp when next request to domain is allowed
  ────────────────────────────────────────
  Key Pattern: doc:{docId}
  Type: Hash
  Description: Indexed document data for search (title, content, embeddings, etc.)
  ────────────────────────────────────────
  Key Pattern: idx:blogs
  Type: RediSearch Index
  Description: Full-text search index over doc:* hashes
