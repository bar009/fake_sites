"""Fetch and cache benign company favicons without contacting suspect websites."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import requests

from fakeshop.whois_check import domain_of


MAX_LOGO_BYTES = 512_000
ALLOWED_REDIRECT_HOSTS = {"icons.duckduckgo.com", "external-content.duckduckgo.com"}
HOST_RE = re.compile(
    r"(?=^.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]{2,63}$"
)
BUSINESS_TERMS = {
    "airline", "apparel", "automotive", "bank", "brand", "business", "chain",
    "clothing", "company", "consumer", "corporation", "cosmetics", "fashion",
    "financial", "footwear", "hotel", "insurance", "jewelry", "label", "luxury",
    "manufacturer", "media", "pharmaceutical", "producer", "restaurant", "retailer",
    "sportswear", "technology", "telecommunications", "watch",
}


def image_media_type(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


class CompanyLogoCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_domain(value: str) -> str:
        host = domain_of(value if "://" in value else f"https://{value}").lower().rstrip(".")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return ""
        return host if HOST_RE.fullmatch(host) else ""

    @staticmethod
    def normalize_name(value: str) -> str:
        folded = unicodedata.normalize("NFKD", value.casefold())
        return "".join(character for character in folded if character.isalnum() and not unicodedata.combining(character))

    def get(self, official_domain: str, company_name: str = "") -> tuple[bytes, str] | None:
        domain = self.normalize_domain(official_domain)
        company_name = company_name.strip()
        if not domain and not company_name:
            return None
        cache_identity = f"domain:{domain}" if domain else f"name-v2:{self.normalize_name(company_name)}"
        key = hashlib.sha256(cache_identity.encode("utf-8")).hexdigest()
        image_path = self.cache_dir / f"{key}.bin"
        missing_path = self.cache_dir / f"{key}.missing"
        if image_path.is_file():
            payload = image_path.read_bytes()
            media_type = image_media_type(payload)
            if media_type:
                return payload, media_type
            image_path.unlink(missing_ok=True)
        if missing_path.is_file():
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                missing_path.stat().st_mtime, timezone.utc,
            )
            if age < timedelta(hours=24):
                return None
            missing_path.unlink(missing_ok=True)

        result = self._download(domain) if domain else self._download_by_name(company_name)
        if result:
            payload, media_type = result
            image_path.write_bytes(payload)
            return payload, media_type
        missing_path.touch()
        return None

    @classmethod
    def _download_by_name(cls, company_name: str) -> tuple[bytes, str] | None:
        headers = {"User-Agent": "FakeShopChecker/1.0 (local research tool)"}
        try:
            search = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities", "search": company_name,
                    "language": "en", "format": "json", "limit": 5,
                },
                timeout=(3, 7), allow_redirects=False, headers=headers,
            )
            if search.status_code != 200:
                return None
            target_key = cls.normalize_name(company_name)
            match = next(
                (
                    item for item in search.json().get("search", [])
                    if cls.normalize_name(item.get("label", "")) == target_key
                    and any(
                        term in re.findall(r"[a-z]+", (item.get("description") or "").casefold())
                        for term in BUSINESS_TERMS
                    )
                ),
                None,
            )
            if not match or not re.fullmatch(r"Q\d+", str(match.get("id", ""))):
                return None
            entity = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities", "ids": match["id"],
                    "props": "claims", "format": "json",
                },
                timeout=(3, 7), allow_redirects=False, headers=headers,
            )
            if entity.status_code != 200:
                return None
            claims = entity.json().get("entities", {}).get(match["id"], {}).get("claims", {})
            for claim in claims.get("P856", []):
                website = (
                    claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
                )
                domain = cls.normalize_domain(str(website))
                if domain:
                    return cls._download(domain)
        except (requests.RequestException, ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _download(domain: str) -> tuple[bytes, str] | None:
        url = f"https://icons.duckduckgo.com/ip3/{quote(domain, safe='.-')}.ico"
        try:
            for _ in range(3):
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
                    return None
                response = requests.get(
                    url, timeout=(3, 6), allow_redirects=False,
                    headers={"User-Agent": "FakeShopChecker/1.0"},
                )
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200 or len(response.content) > MAX_LOGO_BYTES:
                    return None
                media_type = image_media_type(response.content)
                return (response.content, media_type) if media_type else None
        except requests.RequestException:
            return None
        return None
