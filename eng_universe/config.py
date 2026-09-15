import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv("secrets.env", override=False)


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def env_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class KeywordFieldConfig:
    name: str
    field_type: str
    weight: float | None = None
    nostem: bool = False
    phonetic: str | None = None


KEYWORD_FIELDS: list[KeywordFieldConfig] = [
    KeywordFieldConfig(name="title", field_type="TEXT", weight=2.0),
    KeywordFieldConfig(name="description", field_type="TEXT", weight=1.0),
    KeywordFieldConfig(name="subject", field_type="TEXT", weight=2.0, nostem=True),
    KeywordFieldConfig(name="catalogNumber", field_type="TEXT", weight=2.0, nostem=True),
    KeywordFieldConfig(
        name="instructor", field_type="TEXT", weight=1.0, nostem=True, phonetic="dm:en"
    ),
    KeywordFieldConfig(name="component", field_type="TAG"),
    KeywordFieldConfig(name="level", field_type="TAG"),
    KeywordFieldConfig(name="genEdArea", field_type="TAG"),
    KeywordFieldConfig(name="academicYear", field_type="NUMERIC"),
    KeywordFieldConfig(name="content", field_type="TEXT", weight=1.0),
]


class Settings:
    user_agent = os.getenv("EU_USER_AGENT", "EngUniverseBot/0.1")
    redis_url = env("REDIS_URL", "redis://default:devpass@localhost:6379/0")
    crawl_queue_key = os.getenv("CRAWL_QUEUE_KEY", "crawl:queue")
    crawl_delay_key = os.getenv("CRAWL_DELAY_KEY", "crawl:delay")
    raw_queue_key = os.getenv("RAW_QUEUE_KEY", "raw:queue")
    robots_key_prefix = os.getenv("ROBOTS_KEY_PREFIX", "robots:")
    robots_next_allowed_prefix = os.getenv(
        "ROBOTS_NEXT_ALLOWED_PREFIX", "robots:next_allowed:"
    )
    crawl_seen_key = os.getenv("CRAWL_SEEN_KEY", "crawl:seen")
    crawl_doc_seq_key = os.getenv("CRAWL_DOC_SEQ_KEY", "crawl:doc_seq")
    crawl_doc_key_prefix = os.getenv("CRAWL_DOC_KEY_PREFIX", "crawl:doc:")
    crawl_storage_dir = os.getenv("CRAWL_STORAGE_DIR", "data/crawl")
    crawl_depth_limit = int(os.getenv("CRAWL_DEPTH_LIMIT", 3))
    crawl_allow_external = env_bool("CRAWL_ALLOW_EXTERNAL", "false")
    crawl_log = env_bool("CRAWL_LOG", "true")
    seed_domains = os.getenv(
        "SEED_DOMAINS",
        "engineering.fb.com",
    )
    seed_start_urls = os.getenv(
        "SEED_START_URLS",
        "https://engineering.fb.com/",
    )
    max_workers = int(
        os.getenv("MAX_WORKERS") or os.getenv("CRAWLER_CONCURRENCY", "200")
    )
    request_timeout_s = int(os.getenv("REQUEST_TIMEOUT_S", 20))
    crawl_delay_default_s = int(os.getenv("CRAWL_DELAY_DEFAULT_S", 5))
    stage_queue_namespace = os.getenv("STAGE_QUEUE_NAMESPACE", "eu:v1")
    stage_worker_concurrency = int(os.getenv("STAGE_WORKER_CONCURRENCY", "16"))
    stage_lease_ms = int(os.getenv("STAGE_LEASE_MS", "30000"))
    stage_heartbeat_ms = int(os.getenv("STAGE_HEARTBEAT_MS", "10000"))
    stage_reclaim_interval_ms = int(
        os.getenv("STAGE_RECLAIM_INTERVAL_MS", "1000")
    )
    fetch_worker_processes = int(os.getenv("FETCH_WORKER_PROCESSES", "1"))
    fetch_worker_concurrency = int(os.getenv("FETCH_WORKER_CONCURRENCY", "100"))
    fetch_global_connection_limit = int(
        os.getenv("FETCH_GLOBAL_CONNECTION_LIMIT", "100")
    )
    fetch_origin_max_inflight = int(os.getenv("FETCH_ORIGIN_MAX_INFLIGHT", "1"))
    fetch_r2_upload_concurrency = int(
        os.getenv("FETCH_R2_UPLOAD_CONCURRENCY", "8")
    )
    fetch_process_lease_ms = int(os.getenv("FETCH_PROCESS_LEASE_MS", "30000"))
    fetch_process_heartbeat_ms = int(
        os.getenv("FETCH_PROCESS_HEARTBEAT_MS", "10000")
    )
    embeddings_provider = os.getenv("EMBEDDINGS_PROVIDER", "dummy")
    embeddings_dim = int(os.getenv("EMBEDDINGS_DIM", 384))
    keyword_only = env_bool("KEYWORD_ONLY", "false")
    keyword_fields = KEYWORD_FIELDS
    huggingface_api_key = os.getenv("HUGGINGFACE_API_KEY", "")
    huggingface_base_url = os.getenv(
        "HUGGINGFACE_BASE_URL", "https://router.huggingface.co/v1"
    )
    huggingface_provider = os.getenv("HUGGINGFACE_PROVIDER", "auto")
    huggingface_embedding_model = os.getenv(
        "HUGGINGFACE_EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    pylate_model_name = os.getenv(
        "PYLATE_MODEL_NAME", "mixedbread-ai/mxbai-edge-colbert-v0-17m"
    )
    pylate_index_folder = os.getenv("PYLATE_INDEX_FOLDER", "pylate-index")
    pylate_index_name = os.getenv("PYLATE_INDEX_NAME", "index")
    pylate_batch_size = int(os.getenv("PYLATE_BATCH_SIZE", 32))
    pylate_device = os.getenv("PYLATE_DEVICE", "")
    pylate_show_progress = env_bool("PYLATE_SHOW_PROGRESS", "false")
    debug_search = env_bool("DEBUG_SEARCH", "false")
    indexer_exit_on_idle = env_bool("INDEXER_EXIT_ON_IDLE", "true")
    indexer_idle_grace_s = float(os.getenv("INDEXER_IDLE_GRACE_S", "2"))
    metrics_port = int(os.getenv("METRICS_PORT", 9100))
    api_port = int(os.getenv("API_PORT", 8080))
    r2_upload = env_bool("R2_UPLOAD", "false")
    r2_account_id = os.getenv("R2_ACCOUNT_ID", "")
    r2_access_key_id = os.getenv("R2_ACCESS_KEY_ID", "")
    r2_secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "")
    r2_bucket_name = os.getenv("R2_BUCKET_NAME", "")
    r2_region = os.getenv("R2_REGION", "auto")
    r2_endpoint_url = os.getenv("R2_ENDPOINT_URL", "")
