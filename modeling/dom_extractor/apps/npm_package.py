"""Adapter for evaluating the installed npm package against human labels."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from time import perf_counter

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import ParsedPage, parse_html

ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / "dom-tiny-demo"
BRIDGE = DEMO_DIR / "scripts" / "npm-eval-bridge.mjs"
OUTPUT_FIELDS = {
    Field.TITLE: "title",
    Field.ARTICLE: "body",
    Field.DATE: "date",
    Field.AUTHORS: "byline",
}


class NpmPackageExtractor:
    """Call the installed npm package through one persistent Node process."""

    dom_backend = "python"
    author_boundary_checkpoint = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            ["node", str(BRIDGE)],
            cwd=DEMO_DIR,
            env=os.environ.copy(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        handshake = self._process.stdout.readline()
        try:
            ready = json.loads(handshake)
        except json.JSONDecodeError as error:
            self.close()
            raise RuntimeError("npm extractor did not start; refresh dom-tiny-demo") from error
        if not ready.get("ready"):
            self.close()
            raise RuntimeError("npm extractor returned an invalid startup message")
        self.model_version = str(ready["modelVersion"])
        self.checkpoint_display = f"@eng-universe/dom-extractor · {self.model_version}"
        # Browser eval caches must change if selector mapping changes.
        self.cache_identity = f"npm-package:{self.model_version}:raw-selector-v1"

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def predict_page(self, page: ParsedPage) -> dict[Field, int | None]:
        predictions, _confidence, _timings = self.predict_page_profiled_with_confidence(page)
        return predictions

    def predict_page_profiled_with_confidence(
        self, page: ParsedPage
    ) -> tuple[dict[Field, int | None], dict[Field, float], list[dict[str, str | float]]]:
        started = perf_counter()
        request = json.dumps({"html": page.original_html}, ensure_ascii=False)
        with self._lock:
            if self._process.poll() is not None:
                raise RuntimeError("npm extractor process has stopped")
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(request + "\n")
            self._process.stdin.flush()
            response = self._process.stdout.readline()
        elapsed_ms = (perf_counter() - started) * 1_000
        if not response:
            raise RuntimeError("npm extractor closed its output")
        result = json.loads(response)
        if "error" in result:
            raise RuntimeError(f"npm extractor: {result['error']}")

        predictions: dict[Field, int | None] = {}
        confidence: dict[Field, float] = {}
        raw_page: ParsedPage | None = None
        for field in FIELDS:
            output = result["fields"][OUTPUT_FIELDS[field]]
            confidence[field] = float(output["confidence"])
            selector = output.get("source", {}).get("selector")
            node_id = None
            if output.get("text") is not None and selector:
                # The package's selector names a node in raw HTML. Cleanup can
                # delete siblings and shift :nth-of-type positions. Surviving
                # cleaned nodes retain their original IDs.
                if raw_page is None:
                    raw_page = parse_html(page.original_html)
                try:
                    matches = raw_page.dom.select(selector)
                except Exception:  # noqa: BLE001 - tolerate parser-specific CSS
                    matches = []
                if len(matches) == 1:
                    original_id = raw_page.id_by_element_identity.get(id(matches[0]))
                    if original_id in page.node_by_id:
                        node_id = original_id
            predictions[field] = node_id

        return predictions, confidence, [{"step": "npm package inference", "ms": elapsed_ms}]
