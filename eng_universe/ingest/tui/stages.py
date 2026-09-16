"""Stage names and display labels for the queue monitor TUI."""

from __future__ import annotations

from eng_universe.ingest.queue_models import CRAWL_STAGE, INDEX_RAW_STAGE

# Operator-facing aliases map onto the Redis stage name that owns the work.
# Cleaning has no separate queue: it runs inside the index_raw worker path.
STAGE_ALIASES: dict[str, str] = {
    "crawl": CRAWL_STAGE,
    CRAWL_STAGE: CRAWL_STAGE,
    "index": INDEX_RAW_STAGE,
    "index_raw": INDEX_RAW_STAGE,
    INDEX_RAW_STAGE: INDEX_RAW_STAGE,
    "clean": INDEX_RAW_STAGE,
    "cleaning": INDEX_RAW_STAGE,
    "clean_parse": INDEX_RAW_STAGE,
}

STAGE_TITLES: dict[str, str] = {
    CRAWL_STAGE: "Crawl Monitor",
    INDEX_RAW_STAGE: "Index · Clean Monitor",
}

STAGE_TABLE_HEADERS: dict[str, tuple[str, str, str, str]] = {
    CRAWL_STAGE: ("Run ID", "Domain", "Status", "URL"),
    INDEX_RAW_STAGE: ("Run ID", "Doc ID", "Status", "Clean / Detail"),
}


def resolve_stage(name: str) -> str:
    """Returns the canonical Redis stage name for one operator alias."""

    key = name.strip().lower()
    if not key:
        raise ValueError("stage name must not be empty")
    resolved = STAGE_ALIASES.get(key)
    if resolved is None:
        supported = ", ".join(sorted(STAGE_ALIASES))
        raise ValueError(f"unknown stage {name!r}; supported: {supported}")
    return resolved


def stage_title(stage: str) -> str:
    return STAGE_TITLES.get(stage, f"{stage} Monitor")


def stage_table_headers(stage: str) -> tuple[str, str, str, str]:
    return STAGE_TABLE_HEADERS.get(stage, ("Run ID", "Subject", "Status", "Detail"))
