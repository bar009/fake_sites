"""Shared brand canonicalisation for storage and search."""

from __future__ import annotations

import unicodedata


CANONICAL_BRANDS = {
    "duckcamp": "Duck Camp",
}


def brand_key(name: str) -> str:
    normalised = unicodedata.normalize("NFKC", name).casefold()
    return "".join(char for char in normalised if char.isalnum())


def canonical_brand_name(name: str) -> str:
    cleaned = " ".join(unicodedata.normalize("NFKC", name).split())
    return CANONICAL_BRANDS.get(brand_key(cleaned), cleaned)
