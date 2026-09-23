"""A dataset backup must carry the HTML that makes node IDs meaningful."""

import tarfile
from pathlib import Path

import pytest

from modeling.dom_extractor.commands.backup_labeled_data import backup_labeled_data


def test_backup_contains_html_labels_manifest_and_removed_pages(tmp_path: Path) -> None:
    dataset = tmp_path / "raw"
    for name in ("html", "annotations", "jev_annotations", "removed"):
        (dataset / name).mkdir(parents=True)
    (dataset / "manifest.json").write_text('{"pages": []}', encoding="utf-8")
    (dataset / "html" / "one.html").write_text("<article>One</article>", encoding="utf-8")
    (dataset / "annotations" / "one.json").write_text("{}", encoding="utf-8")
    (dataset / "jev_annotations" / "one.json").write_text("{}", encoding="utf-8")
    (dataset / "removed" / "old.html").write_text("old", encoding="utf-8")

    result = backup_labeled_data(dataset, tmp_path / "backups")
    archive = Path(str(result["archive"]))
    assert archive.exists()
    assert result["files"] == 5
    assert len(str(result["sha256"])) == 64
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        assert names == {
            "raw/manifest.json",
            "raw/html/one.html",
            "raw/annotations/one.json",
            "raw/jev_annotations/one.json",
            "raw/removed/old.html",
        }
        assert tar.extractfile("raw/html/one.html").read() == b"<article>One</article>"


def test_backup_rejects_output_inside_source(tmp_path: Path) -> None:
    dataset = tmp_path / "raw"
    (dataset / "html").mkdir(parents=True)
    (dataset / "annotations").mkdir()
    (dataset / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        backup_labeled_data(dataset, dataset / "backups")
