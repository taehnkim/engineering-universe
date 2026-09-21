import struct
from dataclasses import dataclass

import redis.asyncio as redis

from eng_universe.config import KeywordFieldConfig, Settings
from eng_universe.search.embeddings import get_embedding_provider, normalize_embedding
from eng_universe.index.entities import extract_topics
from eng_universe.ingest.etl import ParsedDocument
from eng_universe.monitoring.logging_utils import get_event_logger
from eng_universe.monitoring.metrics import record_index
from eng_universe.search.pylate_backend import add_documents as pylate_add_documents

log_event = get_event_logger("indexer")


@dataclass
class IndexRecord:
    doc_id: str
    title: str
    content: str
    topics: list[str]
    source: str
    company: str
    authors: list[str]
    summary: str | None
    published_at: str | None
    relative_date: str | None
    scraped_at: str
    url: str
    lang: str | None
    embedding: bytes | None


def vector_to_bytes(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _schema_for_field(field: KeywordFieldConfig) -> list[object]:
    parts: list[object] = [field.name, field.field_type]
    if field.field_type == "TEXT":
        if field.weight is not None:
            parts.extend(["WEIGHT", str(field.weight)])
        if field.nostem:
            parts.append("NOSTEM")
        if field.phonetic:
            parts.extend(["PHONETIC", field.phonetic])
    elif field.field_type == "TAG":
        parts.extend(["SEPARATOR", ","])
    return parts


async def index_document(
    redis_client: redis.Redis, doc: ParsedDocument, source: str
) -> None:
    log_event("index", url=doc.url, title=doc.title, source=source)
    embedding_bytes: bytes | None = None
    provider_name = Settings.embeddings_provider.lower()
    if not Settings.keyword_only:
        if provider_name in {"pylate", "colbert"}:
            pylate_add_documents([doc.url], [f"{doc.title}\n{doc.content}"])
        else:
            provider = get_embedding_provider()
            embedding = provider.embed(f"{doc.title}\n{doc.content}")
            vector = normalize_embedding(embedding.vector, Settings.embeddings_dim)
            embedding_bytes = vector_to_bytes(vector)
    record = IndexRecord(
        doc_id=doc.url,
        title=doc.title,
        content=doc.content,
        topics=extract_topics(doc.content),
        source=source,
        company=doc.company,
        authors=doc.authors,
        summary=doc.summary,
        published_at=doc.published_at,
        relative_date=doc.relative_date,
        scraped_at=doc.scraped_at,
        url=doc.canonical_url or doc.url,
        lang=doc.language,
        embedding=embedding_bytes,
    )
    mapping = {
        "doc_id": record.doc_id,
        "title": record.title,
        "content": record.content,
        "topics": ",".join(record.topics),
        "source": record.source,
        "company": record.company,
        "authors": ",".join(record.authors),
        "summary": record.summary or "",
        "published_at": record.published_at or "",
        "relative_date": record.relative_date or "",
        "scraped_at": record.scraped_at,
        "url": record.url,
        "lang": record.lang or "",
    }
    keyword_field_names = [field.name for field in Settings.keyword_fields]
    if keyword_field_names:
        existing_values = await redis_client.hmget(
            f"doc:{record.doc_id}", keyword_field_names
        )
        for name, value in zip(keyword_field_names, existing_values):
            if mapping.get(name):
                continue
            if value is None:
                continue
            if isinstance(value, (bytes, bytearray)):
                decoded = value.decode()
            else:
                decoded = str(value)
            if decoded:
                mapping[name] = decoded
    if record.embedding is not None:
        mapping["embedding"] = record.embedding
    await redis_client.hset(f"doc:{record.doc_id}", mapping=mapping)
    record_index()


async def create_search_index(redis_client: redis.Redis, index_name: str) -> None:
    provider_name = Settings.embeddings_provider.lower()
    if provider_name in {"pylate", "colbert"}:
        from eng_universe.search.pylate_backend import create_plaid_index

        log_event(
            "init-index",
            backend="pylate",
            index=f"{Settings.pylate_index_folder}/{Settings.pylate_index_name}",
        )
        create_plaid_index(override=True)
        log_event("ready", backend="pylate")
    vector_dim = Settings.embeddings_dim
    try:
        log_event(
            "init-index",
            backend="redis",
            index=index_name,
            dim=vector_dim,
        )
        schema: list[object] = []
        keyword_fields = Settings.keyword_fields
        keyword_names = {field.name for field in keyword_fields}
        for field in keyword_fields:
            schema.extend(_schema_for_field(field))
        for name, field_type in (
            ("topics", "TAG"),
            ("source", "TAG"),
            ("company", "TAG"),
            ("authors", "TAG"),
            ("summary", "TEXT"),
            ("published_at", "TEXT"),
            ("relative_date", "TEXT"),
            ("scraped_at", "TEXT"),
            ("url", "TEXT"),
            ("lang", "TAG"),
        ):
            if name in keyword_names:
                continue
            schema.extend(
                _schema_for_field(KeywordFieldConfig(name=name, field_type=field_type))
            )
        if not Settings.keyword_only and provider_name not in {"pylate", "colbert"}:
            schema.extend(
                [
                    "embedding",
                    "VECTOR",
                    "HNSW",
                    "6",
                    "TYPE",
                    "FLOAT32",
                    "DIM",
                    vector_dim,
                    "DISTANCE_METRIC",
                    "COSINE",
                ]
            )
        await redis_client.execute_command(
            "FT.CREATE",
            index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "doc:",
            "NOOFFSETS",  # Skip term offsets (saves memory, disables search result highlighting)
            "SCHEMA",
            *schema,
        )
        log_event("ready", backend="redis", index=index_name)
    except redis.ResponseError as exc:
        if "Index already exists" not in str(exc):
            raise
        log_event("ready", backend="redis", index=index_name, status="exists")
