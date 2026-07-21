"""Shared scanner used by both the CLI and the local web worker."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fakeshop.capture import Capturer
from fakeshop.search import build_query, get_provider
from fakeshop.whois_check import WhoisChecker, domain_of


STATIC_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico",
               ".pdf", ".zip", ".css", ".js", ".mp4", ".woff", ".woff2")


def safe_name(text: str) -> str:
    import re
    return re.sub(r"[^\w.-]+", "_", text).strip("_")[:60] or "unknown"


def capture_target(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(STATIC_EXTS) or "/wp-content/uploads/" in path:
        return f"{parsed.scheme}://{parsed.netloc}/", "search hit was a file; captured site homepage"
    return url, ""


class ScanEngine:
    def __init__(self, provider_name: str = "ddgs"):
        self.provider = get_provider(provider_name)
        self.whois = WhoisChecker()

    def scan_brand(self, brand: str, *, top: int, screenshot_dir: Path,
                   capturer: Capturer) -> list[dict]:
        query = build_query(brand)
        results = [
            hit for hit in self.provider.search(query, top=top)
            if domain_of(hit.url).endswith(".shop")
        ]
        rows = []
        for rank, hit in enumerate(results, start=1):
            rows.append(self._inspect(
                brand=brand, url=hit.url, rank=rank, screenshot_dir=screenshot_dir,
                capturer=capturer, query=query, search_title=hit.title,
                search_snippet=hit.snippet,
            ))
        return rows

    def scan_url(self, brand: str, url: str, *, screenshot_dir: Path,
                 capturer: Capturer) -> list[dict]:
        return [self._inspect(
            brand=brand, url=url, rank=1, screenshot_dir=screenshot_dir,
            capturer=capturer, query="", search_title="", search_snippet="",
        )]

    def _inspect(self, *, brand: str, url: str, rank: int, screenshot_dir: Path,
                 capturer: Capturer, query: str, search_title: str,
                 search_snippet: str) -> dict:
        domain = domain_of(url)
        info = self.whois.lookup(url)
        target_url, note = capture_target(url)
        shot_path = screenshot_dir / f"{safe_name(brand)}_{rank}_{safe_name(domain)}.png"
        cap = capturer.capture(target_url, shot_path)

        root_url = f"https://{domain}/"
        if cap.error and target_url != root_url:
            cap = capturer.capture(root_url, shot_path)
            note = (note + "; " if note else "") + "original URL failed; captured site homepage"

        errors = "; ".join(value for value in (info.error, cap.error) if value)
        return {
            "brand": brand,
            "rank": rank,
            "query": query,
            "url": url,
            "final_url": cap.final_url,
            "http_status": cap.http_status,
            "page_title": cap.page_title or search_title,
            "page_text": cap.page_text,
            "search_title": search_title,
            "search_snippet": search_snippet,
            "domain": domain,
            "domain_created": info.created,
            "domain_age_days": info.age_days,
            "registrar": info.registrar,
            "country": info.country,
            "flags": list(info.flags),
            "note": note,
            "screenshot": cap.screenshot,
            "screenshot_path": str(shot_path) if cap.screenshot else "",
            "error": errors,
        }
