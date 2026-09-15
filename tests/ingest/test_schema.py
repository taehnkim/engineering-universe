from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
from threading import Barrier
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from eng_universe.ingest.models import (
    Document,
    DocumentDiscovery,
    DocumentFetch,
    DocumentURL,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "artifacts",
    "document_discoveries",
    "document_fetches",
    "document_relations",
    "document_urls",
    "documents",
    "stage_runs",
}


class IngestionSchemaTests(unittest.TestCase):
    engine: Engine
    database_url: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.getenv("TEST_DATABASE_URL", "")
        if not cls.database_url:
            raise unittest.SkipTest(
                "TEST_DATABASE_URL must identify a disposable PostgreSQL database"
            )

        cls.engine = create_engine(cls.database_url)
        cls._migrate("base")
        cls._migrate("head")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    @classmethod
    def _migrate(cls, revision: str) -> None:
        config = Config(REPOSITORY_ROOT / "alembic.ini")
        config.set_main_option("sqlalchemy.url", cls.database_url)
        previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = cls.database_url
        try:
            if revision == "base":
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
        finally:
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url

    def test_migration_runs_forward_and_backward_on_empty_database(self) -> None:
        self._migrate("base")
        tables_after_downgrade = set(inspect(self.engine).get_table_names())
        self.assertTrue(EXPECTED_TABLES.isdisjoint(tables_after_downgrade))

        self._migrate("head")
        tables_after_upgrade = set(inspect(self.engine).get_table_names())
        self.assertTrue(EXPECTED_TABLES.issubset(tables_after_upgrade))

    def test_schema_has_identity_due_lease_and_retry_indexes(self) -> None:
        inspector = inspect(self.engine)

        url_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("document_urls")
        }
        run_unique_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("stage_runs")
        }
        document_indexes = {
            index_definition["name"]
            for index_definition in inspector.get_indexes("documents")
        }
        stage_run_indexes = {
            index_definition["name"]
            for index_definition in inspector.get_indexes("stage_runs")
        }

        self.assertIn("uq_document_urls_normalized_url", url_unique_constraints)
        self.assertIn("uq_stage_runs_idempotency_key", run_unique_constraints)
        self.assertIn("ix_documents_due", document_indexes)
        self.assertIn("ix_documents_lease_expiry", document_indexes)
        self.assertIn("ix_stage_runs_retry_due", stage_run_indexes)
        self.assertIn("ix_stage_runs_lease_expiry", stage_run_indexes)

        for table_name in EXPECTED_TABLES:
            for column in inspector.get_columns(table_name):
                if isinstance(column["type"], type(Document.created_at.type)):
                    self.assertTrue(column["type"].timezone)

    def test_concurrent_normalized_url_upserts_return_one_identity(self) -> None:
        normalized_url = "https://example.com/engineering/concurrency"
        with self.engine.begin() as connection:
            document_id = connection.execute(
                insert(Document).returning(Document.id)
            ).scalar_one()

        worker_count = 8
        barrier = Barrier(worker_count)

        def upsert_url(worker_number: int) -> object:
            barrier.wait()
            statement = (
                insert(DocumentURL)
                .values(
                    document_id=document_id,
                    normalized_url=normalized_url,
                    original_url=f"{normalized_url}#worker-{worker_number}",
                )
                .on_conflict_do_update(
                    constraint="uq_document_urls_normalized_url",
                    set_={"last_seen_at": func.now()},
                )
                .returning(DocumentURL.document_id)
            )
            with self.engine.begin() as connection:
                return connection.execute(statement).scalar_one()

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            returned_ids = set(executor.map(upsert_url, range(worker_count)))

        self.assertEqual(returned_ids, {document_id})
        with self.engine.connect() as connection:
            identity_count = connection.execute(
                select(func.count())
                .select_from(DocumentURL)
                .where(DocumentURL.normalized_url == normalized_url)
            ).scalar_one()
        self.assertEqual(identity_count, 1)

    def test_history_remains_when_current_projection_changes(self) -> None:
        now = datetime.now(timezone.utc)
        normalized_url = "https://example.com/engineering/history"
        with self.engine.begin() as connection:
            document_id = connection.execute(
                insert(Document).returning(Document.id)
            ).scalar_one()
            document_url_id = connection.execute(
                insert(DocumentURL)
                .values(
                    document_id=document_id,
                    normalized_url=normalized_url,
                    original_url=normalized_url,
                )
                .returning(DocumentURL.id)
            ).scalar_one()
            connection.execute(
                insert(DocumentDiscovery),
                [
                    {
                        "document_id": document_id,
                        "source_key": "meta-rss",
                        "source_type": "rss_atom",
                        "source_config_version": "sources-1",
                        "discovered_url": normalized_url,
                        "discovered_at": now,
                    },
                    {
                        "document_id": document_id,
                        "source_key": "meta-sitemap",
                        "source_type": "sitemap",
                        "source_config_version": "sources-1",
                        "discovered_url": normalized_url,
                        "discovered_at": now,
                    },
                ],
            )
            fetch_ids = connection.execute(
                insert(DocumentFetch)
                .values(
                    [
                        {
                            "document_id": document_id,
                            "document_url_id": document_url_id,
                            "requested_url": normalized_url,
                            "outcome": "succeeded",
                            "status_code": 200,
                            "started_at": now,
                            "completed_at": now,
                        },
                        {
                            "document_id": document_id,
                            "document_url_id": document_url_id,
                            "requested_url": normalized_url,
                            "outcome": "not_modified",
                            "status_code": 304,
                            "started_at": now,
                            "completed_at": now,
                        },
                    ]
                )
                .returning(DocumentFetch.id)
            ).scalars().all()
            connection.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(current_fetch_id=fetch_ids[-1], fetch_state="fetched")
            )

        with self.engine.connect() as connection:
            fetch_count = connection.execute(
                select(func.count())
                .select_from(DocumentFetch)
                .where(DocumentFetch.document_id == document_id)
            ).scalar_one()
            discovery_count = connection.execute(
                select(func.count())
                .select_from(DocumentDiscovery)
                .where(DocumentDiscovery.document_id == document_id)
            ).scalar_one()
            current_fetch_id = connection.execute(
                select(Document.current_fetch_id).where(Document.id == document_id)
            ).scalar_one()

        self.assertEqual(fetch_count, 2)
        self.assertEqual(discovery_count, 2)
        self.assertEqual(current_fetch_id, fetch_ids[-1])

    def test_database_timestamps_are_timezone_aware(self) -> None:
        with self.engine.begin() as connection:
            created_at = connection.execute(
                insert(Document).returning(Document.created_at)
            ).scalar_one()

        self.assertIsNotNone(created_at.tzinfo)
        self.assertEqual(
            created_at.astimezone(timezone.utc).utcoffset(),
            timezone.utc.utcoffset(created_at),
        )


if __name__ == "__main__":
    unittest.main()
