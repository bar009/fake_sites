"""Search providers for fake-shop fingerprint queries.

Default provider is DuckDuckGo (ddgs, no API key). Brave Search API is
available as an alternative when BRAVE_API_KEY is set in .env.
"""

import os
import random
import time
from dataclasses import dataclass

import requests

# The misspelled "Costumers" is deliberate - it's the fingerprint of the
# fake-shop template being hunted. Add more phrases here as new templates appear.
QUERY_TEMPLATE = 'site:.shop "What Are The Costumers Say" "{brand}"'

MAX_ATTEMPTS = 3


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


def build_query(brand: str) -> str:
    return QUERY_TEMPLATE.format(brand=brand)


class DdgsProvider:
    """DuckDuckGo via the ddgs library. No API key, but rate-limited on
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
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with DDGS() as ddgs:
                    raw = list(ddgs.text(query, max_results=top))
                # DDG intermittently returns an empty set for queries that do
                # have hits - treat empty as retryable until attempts run out.
                if not raw and attempt < MAX_ATTEMPTS:
                    time.sleep(8 * attempt + random.uniform(0, 4))
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
                return results
            except Exception as e:  # noqa: BLE001 - rate limits surface as generic errors
                last_error = e
                if attempt < MAX_ATTEMPTS:
                    time.sleep(10 * attempt + random.uniform(0, 5))
        raise RuntimeError(f"DuckDuckGo search failed after {MAX_ATTEMPTS} attempts: {last_error}")


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
