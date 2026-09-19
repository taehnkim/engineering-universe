#!/usr/bin/env python3
"""Run the learned DOM extractor against a small cross-website sample."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import textwrap
import time
from typing import Sequence

from bs4 import BeautifulSoup

from eng_universe.extraction import DOMExtractor
from eng_universe.extraction.contract import FIELDS as EXTRACTION_FIELDS
from eng_universe.extraction.manifest import DatasetManifest, PageRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = PROJECT_ROOT / "data" / "learned_extraction" / "raw"
DEFAULT_CHECKPOINT = PROJECT_ROOT / "data" / "learned_extraction" / "model" / "best.pt"
FIELDS = tuple(field.value for field in EXTRACTION_FIELDS)
SPACE_RE = re.compile(r"\s+")


class Terminal:
    def __init__(self, color: bool) -> None:
        self.color = color

    def style(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def heading(self, text: str) -> str:
        return self.style(text, "1;36")

    def selected(self, text: str) -> str:
        return self.style(text, "1;32")

    def missing(self, text: str) -> str:
        return self.style(text, "1;33")

    def muted(self, text: str) -> str:
        return self.style(text, "2")

    def file_link(self, path: Path) -> str:
        resolved = path.resolve()
        if sys.stdout.isatty():
            # OSC 8 links are clickable in modern terminals. The visible text
            # remains the absolute path for terminals that ignore the escape.
            return f"\033]8;;{resolved.as_uri()}\033\\{resolved}\033]8;;\033\\"
        return str(resolved)


def _cross_website_sample(records: Sequence[PageRecord], limit: int) -> list[PageRecord]:
    """Select one article per website before taking additional pages."""

    articles = sorted(
        (record for record in records if record.is_article),
        key=lambda record: (record.website, record.page_id),
    )
    selected: list[PageRecord] = []
    selected_ids: set[str] = set()
    seen_websites: set[str] = set()
    for record in articles:
        if record.website not in seen_websites:
            selected.append(record)
            selected_ids.add(record.page_id)
            seen_websites.add(record.website)
        if len(selected) == limit:
            return selected
    for record in articles:
        if record.page_id not in selected_ids:
            selected.append(record)
        if len(selected) == limit:
            break
    return selected


def _tag_name(selected_html: str) -> str:
    element = BeautifulSoup(selected_html, "html.parser").find(True)
    return element.name if element is not None else "unknown"


def _snippet(text: str, width: int) -> str:
    normalized = SPACE_RE.sub(" ", text).strip()
    return textwrap.shorten(normalized, width=width, placeholder=" …") or "(empty text)"


def run(
    dataset_dir: Path,
    checkpoint: Path,
    limit: int,
    fields: Sequence[str],
    snippet_width: int,
    terminal: Terminal,
) -> int:
    manifest_path = dataset_dir / "manifest.json"
    if not checkpoint.exists():
        print(f"Checkpoint not found: {checkpoint}", file=sys.stderr)
        return 2
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    manifest = DatasetManifest.load(manifest_path)
    pages = _cross_website_sample(manifest.pages, limit)
    if not pages:
        print("No article pages found in the manifest.", file=sys.stderr)
        return 2

    print(terminal.heading("DOM extraction inference smoke test"))
    print(terminal.muted(f"Checkpoint: {checkpoint}"))
    print(terminal.muted(f"Inputs: {len(pages)} rendered HTML pages\n"))

    selected_count = 0
    missing_count = 0
    inference_latencies: list[float] = []
    total_started = time.perf_counter()
    extractor = DOMExtractor(checkpoint)

    for index, record in enumerate(pages, start=1):
        html_path = dataset_dir / record.html_path
        html = html_path.read_text(encoding="utf-8")
        started = time.perf_counter()
        results = extractor.extract_all(html)
        elapsed_ms = (time.perf_counter() - started) * 1000
        inference_latencies.append(elapsed_ms)

        print(terminal.heading(f"[{index:02d}/{len(pages):02d}] {record.company} — {record.website}"))
        print(terminal.muted(f"         {record.page_id} · inference={elapsed_ms:.1f} ms"))
        print(f"         input: {terminal.file_link(html_path)}")
        for field in fields:
            result = results[field]
            label = f"  {field.upper():7}"
            if result is None:
                missing_count += 1
                print(f"{label} {terminal.missing('MISSING')}")
                continue
            selected_count += 1
            tag = _tag_name(str(result["html"]))
            classification = terminal.selected(
                f"SELECTED  node={result['node_id']}  tag=<{tag}>"
            )
            print(f"{label} {classification}")
            print(f"           {_snippet(str(result['text']), snippet_width)}")
        print()

    total_ms = (time.perf_counter() - total_started) * 1000
    print(terminal.heading("Summary"))
    print(
        f"  pages={len(pages)}  selected={selected_count}  missing={missing_count}  "
        f"mean_inference={sum(inference_latencies) / len(inference_latencies):.1f} ms  "
        f"total={total_ms:.1f} ms"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--field",
        action="append",
        choices=FIELDS,
        dest="fields",
        help="field to display; repeat for multiple fields (default: all)",
    )
    parser.add_argument("--snippet-width", type=int, default=150)
    parser.add_argument("--no-color", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.snippet_width < 30:
        raise SystemExit("--snippet-width must be at least 30")
    use_color = sys.stdout.isatty() and not args.no_color
    return run(
        args.dataset_dir,
        args.checkpoint,
        args.limit,
        args.fields or FIELDS,
        args.snippet_width,
        Terminal(use_color),
    )


if __name__ == "__main__":
    raise SystemExit(main())
