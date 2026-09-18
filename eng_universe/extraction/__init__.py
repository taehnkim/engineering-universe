"""Learned DOM-node extraction for engineering-blog pages.

Version one intentionally uses structural features and learned HTML-tag
embeddings only. It does not feed article words into the model.
"""

from eng_universe.extraction.inference import DOMExtractor, extract

__all__ = ["DOMExtractor", "extract"]
