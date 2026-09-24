"""Optional in-process Go HTML preparation adapter.

Build the Go shared library separately and set ``ENG_UNIVERSE_GO_DOM_LIBRARY``.
Python remains the default until corpus-wide parity and latency gates pass.
"""

from __future__ import annotations

import ctypes
import json
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4)
def _load_library(path: str) -> ctypes.CDLL:
    library = ctypes.CDLL(path)
    library.PrepareHTML.argtypes = (ctypes.c_void_p, ctypes.c_int)
    library.PrepareHTML.restype = ctypes.c_void_p
    library.FreePreparedHTML.argtypes = (ctypes.c_void_p,)
    library.FreePreparedHTML.restype = None
    return library


def prepare_html(
    html: str, library_path: str | Path | None = None
) -> dict[str, object]:
    """Return cleaned HTML, preview HTML, and stable IDs from Go."""

    path = library_path or os.environ.get("ENG_UNIVERSE_GO_DOM_LIBRARY")
    if not path:
        raise RuntimeError(
            "Go DOM library is not configured; set ENG_UNIVERSE_GO_DOM_LIBRARY"
        )
    library = _load_library(str(Path(path).resolve()))
    encoded = html.encode("utf-8")
    input_buffer = ctypes.create_string_buffer(encoded)
    result_pointer = library.PrepareHTML(input_buffer, len(encoded))
    if not result_pointer:
        raise RuntimeError("Go DOM preparation failed")
    try:
        result = json.loads(ctypes.string_at(result_pointer))
    finally:
        library.FreePreparedHTML(result_pointer)
    if not isinstance(result, dict):
        raise TypeError("Go DOM preparation returned an invalid result")
    return result
