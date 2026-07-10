"""Domain registration lookup via RDAP (the modern WHOIS protocol).

Uses rdap.org, a free redirector that routes to the right registry -
covers .shop and most other TLDs, returns JSON, needs no API key.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

SUSPICIOUS_MAX_AGE_DAYS = 180


@dataclass
class WhoisInfo:
    domain: str
    created: str = ""          # ISO date
    age_days: int | None = None
    registrar: str = ""
    country: str = ""
    error: str = ""
    flags: list[str] = field(default_factory=list)


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host: str) -> str:
    """RDAP registries only know the registered domain, not subdomains
    (onlyintheatres.shop, not marcus.onlyintheatres.shop). Last two labels
    is correct for .shop and the other single-part TLDs this tool targets."""
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) > 2 else host


def _parse_rdap(domain: str, data: dict) -> WhoisInfo:
    info = WhoisInfo(domain=domain)

    for event in data.get("events", []):
        if event.get("eventAction") == "registration" and event.get("eventDate"):
            date_str = event["eventDate"]
            info.created = date_str[:10]
            try:
                created_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                info.age_days = (datetime.now(timezone.utc) - created_dt).days
            except ValueError:
                pass
            break

    for entity in data.get("entities", []):
        roles = entity.get("roles", [])
        vcard = entity.get("vcardArray", [None, []])
        properties = vcard[1] if len(vcard) > 1 and isinstance(vcard[1], list) else []
        if "registrar" in roles and not info.registrar:
            for prop in properties:
                if prop and prop[0] == "fn" and len(prop) > 3 and prop[3]:
                    info.registrar = str(prop[3])
                    break
        if "registrant" in roles and not info.country:
            for prop in properties:
                if prop and prop[0] == "adr" and len(prop) > 3 and isinstance(prop[3], list):
                    if prop[3] and prop[3][-1]:
                        info.country = str(prop[3][-1])
                        break

    if info.age_days is not None and info.age_days < SUSPICIOUS_MAX_AGE_DAYS:
        info.flags.append(f"domain younger than {SUSPICIOUS_MAX_AGE_DAYS} days")
    return info


class WhoisChecker:
    def __init__(self):
        self._cache: dict[str, WhoisInfo] = {}

    def lookup(self, url: str) -> WhoisInfo:
        domain = registrable_domain(domain_of(url))
        if not domain:
            return WhoisInfo(domain="", error="could not extract domain from URL")
        if domain in self._cache:
            return self._cache[domain]

        info = None
        for attempt in range(1, 4):
            try:
                resp = requests.get(f"https://rdap.org/domain/{domain}",
                                    timeout=20, headers={"Accept": "application/rdap+json"})
                if resp.status_code == 404:
                    info = WhoisInfo(domain=domain, error="domain not found in RDAP")
                    break
                if resp.status_code == 429 and attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                resp.raise_for_status()
                info = _parse_rdap(domain, resp.json())
                break
            except Exception as e:  # noqa: BLE001 - a failed lookup must not kill the batch
                info = WhoisInfo(domain=domain, error=f"RDAP lookup failed: {e}")
                if attempt < 3:
                    time.sleep(3 * attempt)

        self._cache[domain] = info
        return info
