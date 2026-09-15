import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import math
import unittest

from eng_universe.ingest.contracts import (
    ArtifactRef,
    ExecutionPolicy,
    JsonValue,
    StageContext,
    StageIdentity,
    StageRequest,
    StageResult,
    canonical_json,
    make_run_idempotency_key,
)


@dataclass(frozen=True)
class ExampleInput:
    values: Mapping[str, JsonValue]

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        return self.values


class ExampleStage:
    identity = StageIdentity(name="example", version="1.0.0")

    async def execute(
        self, stage_input: ExampleInput, context: StageContext
    ) -> StageResult[dict[str, JsonValue]]:
        return StageResult(
            output={
                "attempt": context.attempt,
                "input": stage_input.idempotency_payload(),
            }
        )


class StageContractTests(unittest.TestCase):
    def test_canonical_json_sorts_mappings_and_normalizes_tuples(self) -> None:
        first = {"z": 1, "a": {"items": (3, 2, 1), "enabled": True}}
        second = {"a": {"enabled": True, "items": [3, 2, 1]}, "z": 1}

        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(
            canonical_json(first),
            '{"a":{"enabled":true,"items":[3,2,1]},"z":1}',
        )

    def test_canonical_json_rejects_unsupported_values(self) -> None:
        with self.assertRaisesRegex(TypeError, "non-string mapping key"):
            canonical_json({1: "value"})  # type: ignore[dict-item]
        with self.assertRaisesRegex(TypeError, "unsupported value type bytes"):
            canonical_json({"body": b"value"})  # type: ignore[dict-item]
        with self.assertRaisesRegex(ValueError, "non-finite float"):
            canonical_json({"score": math.inf})

    def test_stage_identity_validates_names_and_versions(self) -> None:
        self.assertEqual(
            StageIdentity(name="parse_article", version="1.2.0").name,
            "parse_article",
        )
        with self.assertRaisesRegex(ValueError, "stage name"):
            StageIdentity(name="ParseArticle", version="1.2.0")
        with self.assertRaisesRegex(ValueError, "stage version"):
            StageIdentity(name="parse_article", version="")

    def test_artifact_reference_validates_content_identity(self) -> None:
        artifact = ArtifactRef(
            artifact_id="artifact-1",
            kind="raw_http",
            schema_version="1",
            content_sha256="a" * 64,
            object_key=f"raw-http/v1/sha256/aa/{'a' * 64}.body",
            content_type="text/html",
            byte_size=10,
        )

        self.assertEqual(artifact.byte_size, 10)
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            ArtifactRef(
                artifact_id="artifact-2",
                kind="raw_http",
                schema_version="1",
                content_sha256="A" * 64,
                object_key="raw-http/invalid.body",
                content_type="text/html",
                byte_size=10,
            )

    def test_idempotency_key_is_stable_for_semantic_input(self) -> None:
        identity = StageIdentity(name="parse_article", version="1.0.0")
        first = StageRequest(
            identity=identity,
            stage_input=ExampleInput({"fetch_id": "fetch-1", "options": {"b": 2, "a": 1}}),
            config_version="sources-1",
        )
        second = StageRequest(
            identity=identity,
            stage_input=ExampleInput({"options": {"a": 1, "b": 2}, "fetch_id": "fetch-1"}),
            config_version="sources-1",
        )

        self.assertEqual(first.idempotency_key(), second.idempotency_key())

    def test_idempotency_key_changes_with_semantic_versions_and_input(self) -> None:
        base = StageRequest(
            identity=StageIdentity(name="parse_article", version="1.0.0"),
            stage_input=ExampleInput({"fetch_id": "fetch-1"}),
            config_version="sources-1",
        )
        changed_stage = StageRequest(
            identity=StageIdentity(name="parse_article", version="2.0.0"),
            stage_input=ExampleInput({"fetch_id": "fetch-1"}),
            config_version="sources-1",
        )
        changed_config = StageRequest(
            identity=base.identity,
            stage_input=base.stage_input,
            config_version="sources-2",
        )
        changed_input = StageRequest(
            identity=base.identity,
            stage_input=ExampleInput({"fetch_id": "fetch-2"}),
            config_version="sources-1",
        )

        keys = {
            base.idempotency_key(),
            changed_stage.idempotency_key(),
            changed_config.idempotency_key(),
            changed_input.idempotency_key(),
        }
        self.assertEqual(len(keys), 4)

    def test_force_policy_is_separate_and_non_promoting_by_default(self) -> None:
        base_key = "parse_article:1.0.0:abc"
        normal_policy = ExecutionPolicy()
        forced_policy = ExecutionPolicy.forced("manual-run-1")

        self.assertEqual(make_run_idempotency_key(base_key, normal_policy), base_key)
        self.assertNotEqual(
            make_run_idempotency_key(base_key, forced_policy),
            base_key,
        )
        self.assertFalse(forced_policy.promote)
        with self.assertRaisesRegex(ValueError, "rerun_nonce"):
            ExecutionPolicy(force=True)

    def test_stage_contract_executes_without_transport_dependencies(self) -> None:
        result = asyncio.run(
            ExampleStage().execute(
                ExampleInput({"fetch_id": "fetch-1"}),
                StageContext(
                    run_id="run-1",
                    attempt=1,
                    policy=ExecutionPolicy(),
                ),
            )
        )

        self.assertEqual(result.output["attempt"], 1)
        self.assertEqual(result.artifacts, ())


if __name__ == "__main__":
    unittest.main()
