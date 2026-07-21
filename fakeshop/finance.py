"""Best-effort company mapping and market-cap lookup through yfinance."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from fakeshop.db import Repository, utc_now


SOURCE_NAME = "Yahoo Finance via yfinance (unofficial)"
COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "limited",
    "plc", "group", "holdings", "holding", "sa", "ag", "nv",
}


def _normalise_company(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return " ".join(word for word in words if word not in COMPANY_SUFFIXES)


def _candidate_score(brand: str, candidate_name: str) -> float:
    left, right = _normalise_company(brand), _normalise_company(candidate_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if right.startswith(left + " ") or left.startswith(right + " "):
        return 0.9
    return SequenceMatcher(None, left, right).ratio()


class FinanceService:
    cache_ttl = timedelta(hours=24)

    def enrich_brand(self, repository: Repository, brand: dict) -> dict:
        existing = repository.get_mapping(brand["brand_id"])
        if existing and self._fresh(existing):
            return existing

        ticker_override = brand.get("ticker_override", "").strip()
        parent_override = brand.get("parent_company_override", "").strip()
        try:
            if ticker_override:
                return self._fetch_market_cap(
                    repository, brand["brand_id"], ticker_override,
                    parent_override or brand["brand"], "confirmed",
                    existing.get("candidates_json", "[]") if existing else "[]",
                )

            if existing and existing.get("ticker") and existing.get("status") in {"confirmed", "auto_confirmed"}:
                return self._fetch_market_cap(
                    repository, brand["brand_id"], existing["ticker"],
                    existing.get("parent_company") or brand["brand"], existing["status"],
                    existing.get("candidates_json", "[]"),
                )

            return self._discover(repository, brand)
        except Exception as exc:  # finance is enrichment, never a scan blocker
            values = {
                "parent_company": existing.get("parent_company", "") if existing else "",
                "ticker": existing.get("ticker", "") if existing else "",
                "status": existing.get("status", "unavailable") if existing else "unavailable",
                "candidates_json": existing.get("candidates_json", "[]") if existing else "[]",
                "market_cap_usd": existing.get("market_cap_usd") if existing else None,
                "finance_source": SOURCE_NAME,
                "finance_fetched_at": utc_now(),
                "last_error": str(exc)[:500],
            }
            repository.save_mapping(brand["brand_id"], **values)
            return repository.get_mapping(brand["brand_id"])

    def _fresh(self, mapping: dict) -> bool:
        stamp = mapping.get("finance_fetched_at")
        if not stamp:
            return False
        try:
            fetched = datetime.fromisoformat(stamp)
            return datetime.now(timezone.utc) - fetched < self.cache_ttl
        except ValueError:
            return False

    def _discover(self, repository: Repository, brand: dict) -> dict:
        import yfinance as yf

        quotes = yf.Search(brand["brand"], max_results=5, news_count=0).quotes
        candidates = []
        for quote in quotes:
            if str(quote.get("quoteType", "")).upper() != "EQUITY":
                continue
            name = quote.get("longname") or quote.get("shortname") or ""
            symbol = quote.get("symbol") or ""
            if not name or not symbol:
                continue
            candidates.append({
                "ticker": symbol,
                "name": name,
                "exchange": quote.get("exchDisp") or quote.get("exchange") or "",
                "score": round(_candidate_score(brand["brand"], name), 3),
            })
        candidates.sort(key=lambda item: item["score"], reverse=True)

        if not candidates:
            repository.save_mapping(
                brand["brand_id"], status="no_match", candidates_json="[]",
                finance_source=SOURCE_NAME, finance_fetched_at=utc_now(),
            )
            return repository.get_mapping(brand["brand_id"])

        top = candidates[0]
        gap = top["score"] - (candidates[1]["score"] if len(candidates) > 1 else 0)
        candidates_json = json.dumps(candidates, ensure_ascii=False)
        if top["score"] >= 0.9 and gap >= 0.15:
            return self._fetch_market_cap(
                repository, brand["brand_id"], top["ticker"], top["name"],
                "auto_confirmed", candidates_json,
            )

        repository.save_mapping(
            brand["brand_id"], status="needs_review", candidates_json=candidates_json,
            finance_source=SOURCE_NAME, finance_fetched_at=utc_now(),
        )
        return repository.get_mapping(brand["brand_id"])

    def _fetch_market_cap(self, repository: Repository, brand_id: int, ticker: str,
                          company_name: str, status: str, candidates_json: str) -> dict:
        import yfinance as yf

        instrument = yf.Ticker(ticker)
        market_cap = None
        try:
            market_cap = instrument.fast_info.get("market_cap")
        except Exception:
            market_cap = None
        if market_cap is None:
            market_cap = instrument.info.get("marketCap")
        repository.save_mapping(
            brand_id, parent_company=company_name, ticker=ticker, status=status,
            candidates_json=candidates_json, market_cap_usd=market_cap,
            finance_source=SOURCE_NAME, finance_fetched_at=utc_now(),
        )
        return repository.get_mapping(brand_id)

    def confirm_candidate(self, repository: Repository, brand_id: int,
                          ticker: str, company_name: str) -> dict:
        existing = repository.get_mapping(brand_id) or {}
        return self._fetch_market_cap(
            repository, brand_id, ticker.strip(), company_name.strip(), "confirmed",
            existing.get("candidates_json", "[]"),
        )
