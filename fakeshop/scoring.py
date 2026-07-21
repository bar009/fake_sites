"""Explainable suspicion and business-priority scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse


FINGERPRINTS = (
    "what our customer say",
    "what our customers say",
    # Retain the earlier typo-based signature for previously discovered sites.
    "what are the costumers say",
)
SECONDARY_MARKERS = (
    "what are our costumers saying",
    "costumer reviews",
    "over 10,000 happy customers",
    "over 10000 happy customers",
)
COMMERCE_MODIFIERS = {"sale", "outlet", "shop", "store", "official", "clearance"}


@dataclass(frozen=True)
class Evidence:
    code: str
    label: str
    points: int
    detail: str


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for index_a, char_a in enumerate(a, start=1):
        current = [index_a]
        for index_b, char_b in enumerate(b, start=1):
            current.append(min(
                current[-1] + 1,
                previous[index_b] + 1,
                previous[index_b - 1] + (char_a != char_b),
            ))
        previous = current
    return previous[-1]


def _domain_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0] if host else ""


def _brand_domain_signal(brand: str, url: str) -> str:
    brand_norm = _normalise(brand)
    label = _domain_label(url)
    label_norm = _normalise(label)
    if not brand_norm or not label_norm or label_norm == brand_norm:
        return ""

    if _levenshtein(brand_norm, label_norm) <= 2:
        return f"The domain name closely resembles {brand}, but is not identical"

    if brand_norm in label_norm:
        remainder = label_norm.replace(brand_norm, "", 1)
        if remainder in COMMERCE_MODIFIERS or any(mod in remainder for mod in COMMERCE_MODIFIERS):
            return f"The brand name is combined with a commercial term: {label}"
    return ""


def _registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def risk_level(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 30:
        return "medium"
    return "low"


def assess_risk(
    *,
    brand: str,
    url: str,
    final_url: str = "",
    page_text: str = "",
    search_snippet: str = "",
    domain_age_days: int | None = None,
) -> dict:
    """Return a capped score plus the exact evidence that produced it."""
    evidence: list[Evidence] = []
    combined = f"{page_text}\n{search_snippet}".lower()

    matched_fingerprint = next((marker for marker in FINGERPRINTS if marker in combined), "")
    if matched_fingerprint:
        evidence.append(Evidence(
            "template_fingerprint", "Storefront template fingerprint", 40,
            f'Detected the phrase "{matched_fingerprint}"',
        ))

    matched_secondary = [marker for marker in SECONDARY_MARKERS if marker in combined][:2]
    for marker in matched_secondary:
        evidence.append(Evidence(
            "secondary_template_marker", "Secondary template marker", 10,
            f'Detected the phrase "{marker}"',
        ))

    if domain_age_days is not None:
        if domain_age_days <= 180:
            evidence.append(Evidence(
                "young_domain", "Newly registered domain", 25,
                f"The domain was registered {domain_age_days} days ago",
            ))
        elif domain_age_days <= 365:
            evidence.append(Evidence(
                "recent_domain", "Domain registered within the past year", 15,
                f"The domain was registered {domain_age_days} days ago",
            ))

    domain_detail = _brand_domain_signal(brand, url)
    if domain_detail:
        evidence.append(Evidence(
            "brand_domain_pattern", "Brand impersonation domain pattern", 15, domain_detail,
        ))

    if final_url and _registrable_domain(url) != _registrable_domain(final_url):
        evidence.append(Evidence(
            "cross_domain_redirect", "Cross-domain redirect", 10,
            f"The page redirected to {_registrable_domain(final_url)}",
        ))

    score = min(100, sum(item.points for item in evidence))
    return {
        "score": score,
        "level": risk_level(score),
        "evidence": [asdict(item) for item in evidence],
    }


def impact_score(market_cap_usd: int | float | None) -> int | None:
    if market_cap_usd is None or market_cap_usd <= 0:
        return None
    if market_cap_usd < 2_000_000_000:
        return 25
    if market_cap_usd < 10_000_000_000:
        return 50
    if market_cap_usd < 100_000_000_000:
        return 75
    return 100


def priority_score(risk: int, market_cap_usd: int | float | None) -> int:
    impact = impact_score(market_cap_usd)
    if impact is None:
        return risk
    return round((risk * 0.75) + (impact * 0.25))
