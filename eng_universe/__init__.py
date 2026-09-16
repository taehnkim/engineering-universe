"""Eng Universe package."""

# Re-export public API for backwards compatibility
# Ingest
from eng_universe.ingest.crawler import (
    CrawlResult,
    clean_html,
    crawl_worker,
    extract_links,
    extract_text,
    is_allowed_url,
    normalize_url,
    run_crawlers,
    seed_queue,
)
from eng_universe.ingest.etl import ParsedDocument, parse_html
from eng_universe.ingest.queue import (
    CrawlItem,
    delay,
    dequeue,
    enqueue,
)
from eng_universe.ingest.robots import (
    RobotsRules,
    get_or_fetch_robots,
    parse_domain,
    reserve_next_allowed,
)

# Index
from eng_universe.index.entities import extract_topics
from eng_universe.index.indexer import (
    IndexRecord,
    create_search_index,
    index_document,
    vector_to_bytes,
)
from eng_universe.index.pipeline import index_worker

# Search
from eng_universe.search.embeddings import (
    EmbeddingProvider,
    EmbeddingResult,
    get_embedding_provider,
    normalize_embedding,
)
from eng_universe.search.pylate_backend import (
    add_documents,
    create_plaid_index,
    encode_documents,
    encode_queries,
    get_colbert_model,
    get_colbert_retriever,
    get_colbert_stack,
    get_plaid_index,
    retrieve,
)
from eng_universe.search.search import SearchResult, search

# Monitoring
from eng_universe.monitoring.metrics import (
    CRAWL_PAGES,
    INDEX_DOCS,
    SEARCH_LATENCY_MS,
    record_crawl,
    record_index,
)
from eng_universe.monitoring.metrics_server import run_metrics_server

__all__ = [
    # Ingest - crawler
    "CrawlResult",
    "clean_html",
    "crawl_worker",
    "extract_links",
    "extract_text",
    "is_allowed_url",
    "normalize_url",
    "run_crawlers",
    "seed_queue",
    # Ingest - etl
    "ParsedDocument",
    "parse_html",
    # Ingest - queue
    "CrawlItem",
    "delay",
    "dequeue",
    "enqueue",
    # Ingest - robots
    "RobotsRules",
    "get_or_fetch_robots",
    "parse_domain",
    "reserve_next_allowed",
    # Index - entities
    "extract_topics",
    # Index - indexer
    "IndexRecord",
    "create_search_index",
    "index_document",
    "vector_to_bytes",
    # Index - pipeline
    "index_worker",
    # Search - embeddings
    "EmbeddingProvider",
    "EmbeddingResult",
    "get_embedding_provider",
    "normalize_embedding",
    # Search - pylate
    "add_documents",
    "create_plaid_index",
    "encode_documents",
    "encode_queries",
    "get_colbert_model",
    "get_colbert_retriever",
    "get_colbert_stack",
    "get_plaid_index",
    "retrieve",
    # Search - search
    "SearchResult",
    "search",
    # Monitoring - metrics
    "CRAWL_PAGES",
    "INDEX_DOCS",
    "SEARCH_LATENCY_MS",
    "record_crawl",
    "record_index",
    # Monitoring - server
    "run_metrics_server",
]
