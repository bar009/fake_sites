"""Search providers for fake-shop fingerprint queries.

Default provider is ddgs with DuckDuckGo and Yahoo backends (no API key). Brave Search API is
available as an alternative when BRAVE_API_KEY is set in .env.
"""

import os
import random
import time
from dataclasses import dataclass

import requests

from fakeshop.brand_identity import canonical_brand_name

# Primary storefront-template fingerprint. Keep this exact phrase quoted so the
# search engine does not turn it into a broad customer-review query.
QUERY_TEMPLATE = 'site:.shop "What Our Customers Say" {brand_clause}'

MAX_ATTEMPTS = 3
# Yahoo currently indexes the exact storefront phrase more consistently. Keep
# DuckDuckGo as the no-key fallback for brands that Yahoo does not return.
DDGS_BACKENDS = ("yahoo", "duckduckgo")


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


def build_query(brand: str) -> str:
    search_name = canonical_brand_name(brand).replace('"', "")
    brand_clause = f'"{search_name}"'
    return QUERY_TEMPLATE.format(brand_clause=brand_clause)


class DdgsProvider:
    """DuckDuckGo with a Yahoo fallback via ddgs. No API key, but rate-limited on
    large batches, so keep the polite delay between queries."""

    name = "ddgs"

    def __init__(self, delay_range=(4.0, 8.0)):
        self.delay_range = delay_range
        self._first_call = True

    def search(self, query: str, top: int = 3) -> list[SearchResult]:
        from ddgs import DDGS

        if not self._first_call:
            time.sleep(random.uniform(*self.delay_range))
        self._first_call = False

        last_error = None
        completed_backend = False
        for backend in DDGS_BACKENDS:
            try:
                with DDGS() as ddgs:
                    raw = list(ddgs.text(query, max_results=top, backend=backend))
                completed_backend = True
                if not raw:
                    continue
                results = []
                for r in raw[:top]:
                    url = r.get("href") or r.get("url") or ""
                    if not url:
                        continue
                    results.append(SearchResult(
                        url=url,
                        title=r.get("title", ""),
                        snippet=r.get("body", "") or r.get("snippet", ""),
                    ))
                # Some backends occasionally return placeholder records with
                # no URL. They are not real hits; continue to the fallback
                # backend instead of reporting an empty successful search.
                if results:
                    return results
            except Exception as e:  # noqa: BLE001 - rate limits surface as generic errors
                # A backend with no hits is not a scan failure. Try the next
                # no-key backend because their indexes differ substantially.
                if "no results" in str(e).lower():
                    completed_backend = True
                    continue
                last_error = e
        if completed_backend:
            return []
        raise RuntimeError(f"DDGS search failed across all backends: {last_error}")


class BraveProvider:
    """Brave Search API (https://api.search.brave.com/). Free tier is
    ~2000 queries/month and supports the site: operator."""

    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str | None = None, delay_range=(1.2, 2.0)):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("BRAVE_API_KEY is not set (put it in .env) - "
                               "or use --provider ddgs")
        self.delay_range = delay_range
        self._first_call = True

    def search(self, query: str, top: int = 3) -> list[SearchResult]:
        if not self._first_call:
            time.sleep(random.uniform(*self.delay_range))
        self._first_call = False

        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.get(
                    self.ENDPOINT,
                    params={"q": query, "count": top},
                    headers={"X-Subscription-Token": self.api_key,
                             "Accept": "application/json"},
                    timeout=20,
                )
                if resp.status_code == 429:
                    raise RuntimeError("Brave API rate limit (429)")
                resp.raise_for_status()
                items = (resp.json().get("web") or {}).get("results") or []
                return [SearchResult(url=i.get("url", ""),
                                     title=i.get("title", ""),
                                     snippet=i.get("description", ""))
                        for i in items[:top] if i.get("url")]
            except Exception as e:  # noqa: BLE001
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    time.sleep(5 * attempt)
        raise RuntimeError(f"Brave search failed after {MAX_ATTEMPTS} attempts: {last_error}")


def get_provider(name: str):
    if name == "brave":
        return BraveProvider()
    if name == "ddgs":
        return DdgsProvider()
    raise ValueError(f"Unknown search provider: {name}")
