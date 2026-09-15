# Ingestion contract architecture

## Purpose

`contracts.py` defines the common language for future ingestion stages.
It does not fetch, parse, store, or index data.

```text
Python / CLI / worker / API
             |
        StageRequest
             |
       future runner
             |
        Stage.execute
             |
        StageResult
```

All callers use the same contract. This prevents duplicate stage logic.

## Main design

- `StageIdentity` gives each stage a stable name and version.
- `StageInput` exposes only values that can change the result.
- `StageRequest` joins the stage identity, semantic input, and configuration version.
- `ExecutionPolicy` keeps force and promotion controls out of semantic input.
- `StageContext` gives run metadata to the stage.
- `StageResult` returns typed output and artifact references.
- `ArtifactRef` points to an immutable object, such as a Cloudflare R2 object.
- `StageStatus` defines states that a future runner can persist.
- `Stage` is a protocol. A stage does not need to inherit a base class.

Generic input and output types let type checkers find invalid stage connections.
Frozen data classes prevent accidental field reassignment.
Slots keep the contract objects small.

## Idempotency

The key identifies equivalent work:

```text
stage name
    + stage version
    + configuration version
    + semantic input
              |
       canonical JSON
              |
           SHA-256
              |
       idempotency key
```

Canonical JSON sorts mapping keys and rejects unsupported or unstable values.
The same semantic input produces the same key.
A stage, configuration, or input change produces a different key.

A forced run adds a nonce to the run key. It does not change the semantic input.
Forced runs do not promote their result by default.

## Artifact references

Large content does not move through stage requests.
Stages pass an `ArtifactRef` with an object key, content hash, media type, and size.
The hash verifies content identity and supports immutable, content-addressed storage.

## Boundaries

This module has no Redis, HTTP, R2, database, or model client.
Tests can use it without infrastructure.
A later runner will add persistence, leases, retries, and stage scheduling.
