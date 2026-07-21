"""Small SQLite repository used by the local web application."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL DEFAULT '',
    parent_company_override TEXT NOT NULL DEFAULT '',
    ticker_override TEXT NOT NULL DEFAULT '',
    official_domain TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS company_mappings (
    brand_id INTEGER PRIMARY KEY REFERENCES brands(id) ON DELETE CASCADE,
    parent_company TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    market_cap_usd REAL,
    finance_source TEXT NOT NULL DEFAULT '',
    finance_fetched_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    provider TEXT NOT NULL DEFAULT 'ddgs',
    top_n INTEGER NOT NULL DEFAULT 3,
    source_name TEXT NOT NULL DEFAULT '',
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scan_targets (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    input_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    rank INTEGER,
    url TEXT NOT NULL DEFAULT '',
    final_url TEXT NOT NULL DEFAULT '',
    page_title TEXT NOT NULL DEFAULT '',
    search_snippet TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    domain_created TEXT NOT NULL DEFAULT '',
    domain_age_days INTEGER,
    registrar TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'low',
    priority_score INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_targets_scan_idx ON scan_targets(scan_id, status);
CREATE INDEX IF NOT EXISTS findings_scan_idx ON findings(scan_id, priority_score DESC);
"""


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _normalise_brand(name: str) -> str:
        return " ".join(name.lower().split())

    def upsert_brand(self, item: dict) -> int:
        name = item["brand"].strip()
        normalized = self._normalise_brand(name)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO brands(name, normalized_name, topic, parent_company_override,
                       ticker_override, official_domain)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(normalized_name) DO UPDATE SET
                       name=excluded.name,
                       topic=CASE WHEN excluded.topic != '' THEN excluded.topic ELSE brands.topic END,
                       parent_company_override=CASE WHEN excluded.parent_company_override != '' THEN excluded.parent_company_override ELSE brands.parent_company_override END,
                       ticker_override=CASE WHEN excluded.ticker_override != '' THEN excluded.ticker_override ELSE brands.ticker_override END,
                       official_domain=CASE WHEN excluded.official_domain != '' THEN excluded.official_domain ELSE brands.official_domain END""",
                (name, normalized, item.get("topic", ""), item.get("parent_company", ""),
                 item.get("ticker", ""), item.get("official_domain", "")),
            )
            row = connection.execute(
                "SELECT id FROM brands WHERE normalized_name=?", (normalized,)
            ).fetchone()
            return int(row["id"])

    def create_scan(self, *, kind: str, targets: Iterable[dict], provider: str,
                    top_n: int, source_name: str = "") -> int:
        targets = list(targets)
        brand_targets = [(self.upsert_brand(target), target) for target in targets]
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO scan_runs(kind, provider, top_n, source_name,
                       progress_total, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (kind, provider, top_n, source_name, len(targets), utc_now()),
            )
            scan_id = int(cursor.lastrowid)
            for brand_id, target in brand_targets:
                connection.execute(
                    "INSERT INTO scan_targets(scan_id, brand_id, input_url) VALUES (?, ?, ?)",
                    (scan_id, brand_id, target.get("url", "")),
                )
            return scan_id

    def recover_interrupted(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_runs SET status='interrupted' WHERE status='running'"
            )
            connection.execute(
                """UPDATE scan_targets SET status='pending'
                   WHERE status='running' AND scan_id IN
                       (SELECT id FROM scan_runs WHERE status='interrupted')"""
            )

    def claim_next_scan(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM scan_runs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE scan_runs SET status='running', started_at=?, error='' WHERE id=?",
                (utc_now(), row["id"]),
            )
            return dict(row) | {"status": "running"}

    def get_scan(self, scan_id: int):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
            return dict(row) if row else None

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM findings f WHERE f.scan_id=s.id) AS finding_count,
                          (SELECT COUNT(*) FROM findings f WHERE f.scan_id=s.id AND f.risk_level='high') AS high_count
                   FROM scan_runs s ORDER BY s.id DESC LIMIT ?""", (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def pending_targets(self, scan_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT t.*, b.name AS brand, b.topic, b.parent_company_override,
                          b.ticker_override, b.official_domain
                   FROM scan_targets t JOIN brands b ON b.id=t.brand_id
                   WHERE t.scan_id=? AND t.status='pending' ORDER BY t.id""", (scan_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_target_status(self, target_id: int, status: str, error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_targets SET status=?, error=? WHERE id=?", (status, error, target_id)
            )

    def advance_scan(self, scan_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_runs SET progress_current=progress_current+1 WHERE id=?", (scan_id,)
            )

    def finish_scan(self, scan_id: int, status: str = "completed", error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, utc_now(), error, scan_id),
            )

    def request_cancel(self, scan_id: int) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE scan_runs SET cancel_requested=1 WHERE id=?", (scan_id,))

    def should_cancel(self, scan_id: int) -> bool:
        scan = self.get_scan(scan_id)
        return bool(scan and scan["cancel_requested"])

    def resume_scan(self, scan_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
            if not row or row["status"] not in {"cancelled", "failed", "interrupted"}:
                return False
            connection.execute(
                """UPDATE scan_runs SET status='queued', cancel_requested=0,
                       finished_at='', error='' WHERE id=?""", (scan_id,)
            )
            connection.execute(
                "UPDATE scan_targets SET status='pending' WHERE scan_id=? AND status='running'",
                (scan_id,),
            )
            return True

    def add_finding(self, *, scan_id: int, brand_id: int, row: dict,
                    assessment: dict, priority: int) -> int:
        values = (
            scan_id, brand_id, row.get("rank"), row.get("url", ""), row.get("final_url", ""),
            row.get("page_title", ""), row.get("search_snippet", ""), row.get("domain", ""),
            row.get("http_status"), row.get("domain_created", ""), row.get("domain_age_days"),
            row.get("registrar", ""), row.get("country", ""), row.get("screenshot_path", ""),
            row.get("error", ""), assessment["score"], assessment["level"], priority,
            json.dumps(assessment["evidence"], ensure_ascii=False), utc_now(),
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO findings(
                       scan_id, brand_id, rank, url, final_url, page_title, search_snippet,
                       domain, http_status, domain_created, domain_age_days, registrar,
                       country, screenshot_path, error, risk_score, risk_level,
                       priority_score, evidence_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            return int(cursor.lastrowid)

    def list_findings(self, scan_id: int, risk: str = "", review: str = "") -> list[dict]:
        clauses = ["f.scan_id=?"]
        params: list = [scan_id]
        if risk:
            clauses.append("f.risk_level=?")
            params.append(risk)
        if review:
            clauses.append("f.review_status=?")
            params.append(review)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT f.*, b.name AS brand, b.topic, m.parent_company,
                            m.ticker, m.market_cap_usd, m.finance_fetched_at
                     FROM findings f JOIN brands b ON b.id=f.brand_id
                     LEFT JOIN company_mappings m ON m.brand_id=b.id
                     WHERE {' AND '.join(clauses)}
                     ORDER BY f.priority_score DESC, f.id""", params,
            ).fetchall()
            return [self._decode_finding(dict(row)) for row in rows]

    def get_finding(self, finding_id: int):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT f.*, b.name AS brand, b.topic, m.parent_company,
                          m.ticker, m.market_cap_usd, m.finance_fetched_at
                   FROM findings f JOIN brands b ON b.id=f.brand_id
                   LEFT JOIN company_mappings m ON m.brand_id=b.id
                   WHERE f.id=?""", (finding_id,),
            ).fetchone()
            return self._decode_finding(dict(row)) if row else None

    @staticmethod
    def _decode_finding(row: dict) -> dict:
        row["evidence"] = json.loads(row.pop("evidence_json") or "[]")
        return row

    def update_review(self, finding_id: int, status: str, note: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE findings SET review_status=?, review_note=? WHERE id=?",
                (status, note.strip()[:2000], finding_id),
            )

    def delete_scan(self, scan_id: int) -> list[str]:
        with self.connect() as connection:
            paths = [row["screenshot_path"] for row in connection.execute(
                "SELECT screenshot_path FROM findings WHERE scan_id=?", (scan_id,)
            ).fetchall() if row["screenshot_path"]]
            connection.execute("DELETE FROM scan_runs WHERE id=?", (scan_id,))
            return paths

    def get_mapping(self, brand_id: int):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM company_mappings WHERE brand_id=?", (brand_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_mapping(self, brand_id: int, **values) -> None:
        defaults = {
            "parent_company": "", "ticker": "", "status": "pending",
            "candidates_json": "[]", "market_cap_usd": None,
            "finance_source": "", "finance_fetched_at": "", "last_error": "",
        }
        defaults.update(values)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO company_mappings(
                       brand_id, parent_company, ticker, status, candidates_json,
                       market_cap_usd, finance_source, finance_fetched_at, last_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(brand_id) DO UPDATE SET
                       parent_company=excluded.parent_company, ticker=excluded.ticker,
                       status=excluded.status, candidates_json=excluded.candidates_json,
                       market_cap_usd=excluded.market_cap_usd,
                       finance_source=excluded.finance_source,
                       finance_fetched_at=excluded.finance_fetched_at,
                       last_error=excluded.last_error""",
                (brand_id, defaults["parent_company"], defaults["ticker"], defaults["status"],
                 defaults["candidates_json"], defaults["market_cap_usd"],
                 defaults["finance_source"], defaults["finance_fetched_at"], defaults["last_error"]),
            )

    def list_mappings(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT b.id AS brand_id, b.name AS brand, b.topic, m.*
                   FROM brands b LEFT JOIN company_mappings m ON m.brand_id=b.id
                   ORDER BY CASE WHEN m.status='needs_review' THEN 0 ELSE 1 END, b.name"""
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["candidates"] = json.loads(item.get("candidates_json") or "[]")
                result.append(item)
            return result

    def latest_finance_update(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(finance_fetched_at) AS updated FROM company_mappings"
            ).fetchone()
            return row["updated"] or "טרם עודכן"

    def dashboard_stats(self) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM scan_runs) AS scans,
                    (SELECT COUNT(*) FROM findings) AS findings,
                    (SELECT COUNT(*) FROM findings WHERE risk_level='high') AS high,
                    (SELECT COUNT(*) FROM findings WHERE review_status='confirmed') AS confirmed"""
            ).fetchone()
            return dict(row)
