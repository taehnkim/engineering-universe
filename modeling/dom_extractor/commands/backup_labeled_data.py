"""Create and verify a compressed backup of the labeled raw HTML dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def backup_labeled_data(dataset_dir: Path, output_dir: Path) -> dict[str, str | int]:
    dataset_dir = dataset_dir.resolve()
    output_dir = output_dir.resolve()
    if not (dataset_dir / "manifest.json").is_file():
        raise ValueError(f"missing dataset manifest: {dataset_dir / 'manifest.json'}")
    if not (dataset_dir / "annotations").is_dir():
        raise ValueError(f"missing annotation directory: {dataset_dir / 'annotations'}")
    if not (dataset_dir / "html").is_dir():
        raise ValueError(f"missing raw HTML directory: {dataset_dir / 'html'}")
    if output_dir == dataset_dir or dataset_dir in output_dir.parents:
        raise ValueError("backup output must be outside the source dataset")

    files = sorted(path for path in dataset_dir.rglob("*") if path.is_file())
    if not files:
        raise ValueError("dataset has no files to back up")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = output_dir / f"labeled-data-{timestamp}.tar.gz"
    if archive_path.exists():
        raise FileExistsError(f"backup already exists: {archive_path}")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".labeled-data-", suffix=".tar.gz", dir=output_dir, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        with tarfile.open(temporary_path, mode="w:gz") as archive:
            for path in files:
                if path.is_symlink():
                    raise ValueError(f"dataset contains a symlink: {path}")
                archive.add(path, arcname=Path("raw") / path.relative_to(dataset_dir))

        # Read every member. This checks that the gzip stream and tar members
        # are readable before the temporary archive is promoted to a backup.
        verified = 0
        with tarfile.open(temporary_path, mode="r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                assert stream is not None
                while stream.read(1024 * 1024):
                    pass
                verified += 1
        if verified != len(files):
            raise ValueError(f"backup verification found {verified} of {len(files)} files")
        os.link(temporary_path, archive_path)
        temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "archive": str(archive_path),
        "files": len(files),
        "bytes": archive_path.stat().st_size,
        "sha256": digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=Path("data/learned_extraction/raw")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/learned_extraction/backups")
    )
    args = parser.parse_args()
    print(json.dumps(backup_labeled_data(args.dataset_dir, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
