"""Small SQLite repository used by the local web application."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from fakeshop.brand_identity import brand_key, canonical_brand_name
from fakeshop.whois_check import domain_of, registrable_domain


SCHEMA_VERSION = 5


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
    error TEXT NOT NULL DEFAULT '',
    heartbeat_at TEXT NOT NULL DEFAULT '',
    current_target TEXT NOT NULL DEFAULT '',
    recovery_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS scan_targets (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    input_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT ''
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
    search_query TEXT NOT NULL DEFAULT '',
    search_title TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    registrable_domain TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    domain_created TEXT NOT NULL DEFAULT '',
    domain_age_days INTEGER,
    registrar TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    screenshot_path TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    capture_note TEXT NOT NULL DEFAULT '',
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'low',
    priority_score INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    review_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company_outreach (
    company_key TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    brand_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_targets_scan_idx ON scan_targets(scan_id, status);
CREATE INDEX IF NOT EXISTS findings_scan_idx ON findings(scan_id, priority_score DESC);
CREATE INDEX IF NOT EXISTS company_outreach_status_idx ON company_outreach(status, created_at DESC);
"""


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.db_path.exists()
        if existed and self._schema_version() < SCHEMA_VERSION:
            self._backup_database()
        self.init_schema()
        self._migrate()

    def _schema_version(self) -> int:
        if not self.db_path.exists():
            return 0
        with sqlite3.connect(self.db_path) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _backup_database(self) -> None:
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{self.db_path.stem}-v{self._schema_version()}-{stamp}.db"
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)

    def _migrate(self) -> None:
        with self.connect() as connection:
            old_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(findings)").fetchall()
            }
            additions = {
                "search_query": "TEXT NOT NULL DEFAULT ''",
                "search_title": "TEXT NOT NULL DEFAULT ''",
                "registrable_domain": "TEXT NOT NULL DEFAULT ''",
                "capture_note": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE findings ADD COLUMN {name} {declaration}")

            table_additions = {
                "scan_runs": {
                    "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
                    "current_target": "TEXT NOT NULL DEFAULT ''",
                    "recovery_note": "TEXT NOT NULL DEFAULT ''",
                },
                "scan_targets": {
                    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                    "started_at": "TEXT NOT NULL DEFAULT ''",
                    "finished_at": "TEXT NOT NULL DEFAULT ''",
                },
            }
            for table, additions_for_table in table_additions.items():
                existing = {
                    row["name"] for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                for name, declaration in additions_for_table.items():
                    if name not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                        )

            rows = connection.execute(
                "SELECT id, domain FROM findings WHERE registrable_domain=''"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE findings SET registrable_domain=? WHERE id=?",
                    (registrable_domain(row["domain"]), row["id"]),
                )

            self._merge_duplicate_brands(connection)
            if old_version < 3:
                self._translate_historical_evidence(connection)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _translate_historical_evidence(connection: sqlite3.Connection) -> None:
        """Translate known system-generated evidence while preserving unknown/user text."""
        labels = {
            "template_fingerprint": "Storefront template fingerprint",
            "secondary_template_marker": "Secondary template marker",
            "young_domain": "Newly registered domain",
            "recent_domain": "Domain registered within the past year",
            "brand_domain_pattern": "Brand impersonation domain pattern",
            "cross_domain_redirect": "Cross-domain redirect",
        }
        rows = connection.execute(
            "SELECT id, domain, final_url, domain_age_days, evidence_json FROM findings"
        ).fetchall()
        for row in rows:
            evidence = json.loads(row["evidence_json"] or "[]")
            changed = False
            for item in evidence:
                code = item.get("code")
                if code not in labels:
                    continue
                item["label"] = labels[code]
                if code in {"template_fingerprint", "secondary_template_marker"}:
                    detail = item.get("detail", "")
                    marker = detail.split('"')[1] if detail.count('"') >= 2 else "the known template phrase"
                    item["detail"] = f'Detected the phrase "{marker}"'
                elif code in {"young_domain", "recent_domain"}:
                    age = row["domain_age_days"]
                    item["detail"] = (
                        f"The domain was registered {age} days ago"
                        if age is not None else "The domain registration is recent"
                    )
                elif code == "brand_domain_pattern":
                    item["detail"] = f"The domain name uses a brand-like commercial pattern: {row['domain']}"
                elif code == "cross_domain_redirect":
                    redirected = registrable_domain(domain_of(row["final_url"]))
                    item["detail"] = f"The page redirected to {redirected or 'another domain'}"
                changed = True
            if changed:
                connection.execute(
                    "UPDATE findings SET evidence_json=? WHERE id=?",
                    (json.dumps(evidence, ensure_ascii=False), row["id"]),
                )

    @staticmethod
    def _merge_duplicate_brands(connection: sqlite3.Connection) -> None:
        rows = [dict(row) for row in connection.execute("SELECT * FROM brands ORDER BY id")]
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(brand_key(row["name"]), []).append(row)

        status_rank = {
            "confirmed": 6, "auto_confirmed": 5, "needs_review": 4,
            "no_match": 3, "unavailable": 2, "pending": 1,
        }
        for key, group in groups.items():
            if not key:
                continue
            survivor = group[0]
            ids = [item["id"] for item in group]
            placeholders = ",".join("?" for _ in ids)
            mappings = [dict(row) for row in connection.execute(
                f"SELECT * FROM company_mappings WHERE brand_id IN ({placeholders})", ids,
            )]
            chosen_mapping = max(
                mappings,
                key=lambda item: (
                    status_rank.get(item.get("status", "pending"), 0),
                    item.get("finance_fetched_at", ""),
                ),
                default=None,
            )
            candidates = {}
            for mapping in mappings:
                for candidate in json.loads(mapping.get("candidates_json") or "[]"):
                    candidate_key = candidate.get("ticker") or candidate.get("name")
                    if candidate_key:
                        candidates[candidate_key] = candidate

            for duplicate in group[1:]:
                connection.execute(
                    "UPDATE scan_targets SET brand_id=? WHERE brand_id=?",
                    (survivor["id"], duplicate["id"]),
                )
                connection.execute(
                    "UPDATE findings SET brand_id=? WHERE brand_id=?",
                    (survivor["id"], duplicate["id"]),
                )
            connection.execute(
                f"DELETE FROM company_mappings WHERE brand_id IN ({placeholders})", ids,
            )
            if len(group) > 1:
                connection.execute(
                    f"DELETE FROM brands WHERE id IN ({','.join('?' for _ in group[1:])})",
                    [item["id"] for item in group[1:]],
                )

            def first_value(field: str) -> str:
                return next((item[field] for item in group if item.get(field)), "")

            connection.execute(
                """UPDATE brands SET name=?, normalized_name=?, topic=?,
                          parent_company_override=?, ticker_override=?, official_domain=?
                   WHERE id=?""",
                (
                    canonical_brand_name(survivor["name"]), key, first_value("topic"),
                    first_value("parent_company_override"), first_value("ticker_override"),
                    first_value("official_domain"), survivor["id"],
                ),
            )
            if chosen_mapping:
                chosen_mapping["candidates_json"] = json.dumps(
                    list(candidates.values()), ensure_ascii=False,
                )
                connection.execute(
                    """INSERT INTO company_mappings(
                           brand_id, parent_company, ticker, status, candidates_json,
                           market_cap_usd, finance_source, finance_fetched_at, last_error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        survivor["id"], chosen_mapping.get("parent_company", ""),
                        chosen_mapping.get("ticker", ""), chosen_mapping.get("status", "pending"),
                        chosen_mapping["candidates_json"], chosen_mapping.get("market_cap_usd"),
                        chosen_mapping.get("finance_source", ""),
                        chosen_mapping.get("finance_fetched_at", ""),
                        chosen_mapping.get("last_error", ""),
                    ),
                )

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
        return brand_key(name)

    def upsert_brand(self, item: dict) -> int:
        name = canonical_brand_name(item["brand"])
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
                """UPDATE scan_runs
                   SET status='queued', cancel_requested=0, current_target='',
                       heartbeat_at=?, recovery_note='Automatically resumed after the worker stopped.'
                   WHERE status IN ('running','interrupted')""",
                (utc_now(),),
            )
            connection.execute(
                """UPDATE scan_targets
                   SET status='pending', error='', finished_at=''
                   WHERE status='running' AND scan_id IN
                       (SELECT id FROM scan_runs WHERE status='queued')"""
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
                """UPDATE scan_runs SET status='running',
                       started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                       heartbeat_at=?, error='' WHERE id=?""",
                (utc_now(), utc_now(), row["id"]),
            )
            return dict(row) | {"status": "running", "heartbeat_at": utc_now()}

    def get_scan(self, scan_id: int):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
            return dict(row) if row else None

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT s.*,
                          (SELECT COUNT(DISTINCT COALESCE(NULLIF(f.registrable_domain,''), f.domain))
                           FROM findings f WHERE f.scan_id=s.id) AS finding_count,
                          (SELECT COUNT(DISTINCT COALESCE(NULLIF(f.registrable_domain,''), f.domain))
                           FROM findings f WHERE f.scan_id=s.id AND f.risk_level='high') AS high_count
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

    def list_scan_targets(self, scan_id: int) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT t.*, b.name AS brand, b.topic,
                          COUNT(f.id) AS page_count,
                          COUNT(DISTINCT COALESCE(NULLIF(f.registrable_domain,''), f.domain)) AS domain_count
                   FROM scan_targets t JOIN brands b ON b.id=t.brand_id
                   LEFT JOIN findings f ON f.scan_id=t.scan_id AND f.brand_id=t.brand_id
                   WHERE t.scan_id=?
                   GROUP BY t.id ORDER BY t.id""", (scan_id,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["display_status"] = (
                    "no_results" if item["status"] == "completed" and not item["domain_count"]
                    else item["status"]
                )
                result.append(item)
            return result

    def set_target_status(self, target_id: int, status: str, error: str = "") -> None:
        with self.connect() as connection:
            if status == "running":
                connection.execute(
                    """UPDATE scan_targets SET status=?, error=?, started_at=?,
                              finished_at='', attempt_count=attempt_count+1 WHERE id=?""",
                    (status, error, utc_now(), target_id),
                )
            elif status in {"completed", "failed"}:
                connection.execute(
                    "UPDATE scan_targets SET status=?, error=?, finished_at=? WHERE id=?",
                    (status, error, utc_now(), target_id),
                )
            else:
                connection.execute(
                    "UPDATE scan_targets SET status=?, error=? WHERE id=?",
                    (status, error, target_id),
                )

    def heartbeat_scan(self, scan_id: int, current_target: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_runs SET heartbeat_at=?, current_target=? WHERE id=?",
                (utc_now(), current_target[:200], scan_id),
            )

    def advance_scan(self, scan_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE scan_runs SET progress_current=progress_current+1 WHERE id=?", (scan_id,)
            )

    def finish_scan(self, scan_id: int, status: str = "completed", error: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE scan_runs SET status=?, finished_at=?, error=?,
                          heartbeat_at=?, current_target='' WHERE id=?""",
                (status, utc_now(), error, utc_now(), scan_id),
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
                       finished_at='', error='', recovery_note='Resumed manually.' WHERE id=?""",
                (scan_id,)
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
            row.get("page_title", ""), row.get("search_snippet", ""), row.get("query", ""),
            row.get("search_title", ""), row.get("domain", ""),
            row.get("registrable_domain", ""), row.get("http_status"),
            row.get("domain_created", ""), row.get("domain_age_days"),
            row.get("registrar", ""), row.get("country", ""), row.get("screenshot_path", ""),
            row.get("error", ""), row.get("note", ""), assessment["score"], assessment["level"], priority,
            json.dumps(assessment["evidence"], ensure_ascii=False), utc_now(),
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO findings(
                       scan_id, brand_id, rank, url, final_url, page_title, search_snippet,
                       search_query, search_title, domain, registrable_domain, http_status,
                       domain_created, domain_age_days, registrar, country, screenshot_path,
                       error, capture_note, risk_score, risk_level, priority_score,
                       evidence_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            return int(cursor.lastrowid)

    def list_findings(self, scan_id: int, risk: str = "", review: str = "",
                      q: str = "", sort: str = "priority") -> list[dict]:
        clauses = ["f.scan_id=?"]
        params: list = [scan_id]
        if risk:
            clauses.append("f.risk_level=?")
            params.append(risk)
        if review == "open":
            clauses.append("f.review_status IN ('unreviewed','investigate')")
        elif review:
            clauses.append("f.review_status=?")
            params.append(review)
        if q:
            clauses.append("(f.domain LIKE ? OR b.name LIKE ? OR f.page_title LIKE ?)")
            needle = f"%{q.strip()}%"
            params.extend([needle, needle, needle])
        orderings = {
            "priority": "f.priority_score DESC, f.risk_score DESC, f.id",
            "risk": "f.risk_score DESC, f.priority_score DESC, f.id",
            "date": "f.created_at DESC, f.id DESC",
            "domain": "f.domain COLLATE NOCASE, f.id",
        }
        ordering = orderings.get(sort, orderings["priority"])
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT f.*, b.name AS brand, b.topic, m.parent_company,
                            b.official_domain, m.ticker, m.market_cap_usd,
                            m.status AS mapping_status, m.finance_source,
                            m.finance_fetched_at, m.last_error AS finance_error
                     FROM findings f JOIN brands b ON b.id=f.brand_id
                     LEFT JOIN company_mappings m ON m.brand_id=b.id
                     WHERE {' AND '.join(clauses)}
                     ORDER BY {ordering}""", params,
            ).fetchall()
            return [self._decode_finding(dict(row)) for row in rows]

    def list_finding_groups(self, scan_id: int, risk: str = "", review: str = "",
                            q: str = "", sort: str = "priority") -> list[dict]:
        rows = self.list_findings(scan_id, risk=risk, review=review, q=q, sort=sort)
        result = self._group_finding_rows(rows, sort)
        self._attach_company_priorities(result)
        return result

    def list_all_findings(self, risk: str = "", review: str = "", q: str = "",
                          sort: str = "priority") -> list[dict]:
        clauses = ["1=1"]
        params: list = []
        if risk:
            clauses.append("f.risk_level=?")
            params.append(risk)
        if review == "open":
            clauses.append("f.review_status IN ('unreviewed','investigate')")
        elif review:
            clauses.append("f.review_status=?")
            params.append(review)
        if q:
            clauses.append(
                "(f.domain LIKE ? OR f.url LIKE ? OR b.name LIKE ? "
                "OR f.page_title LIKE ? OR m.parent_company LIKE ?)"
            )
            needle = f"%{q.strip()}%"
            params.extend([needle, needle, needle, needle, needle])
        orderings = {
            "priority": "f.priority_score DESC, f.risk_score DESC, f.id DESC",
            "risk": "f.risk_score DESC, f.priority_score DESC, f.id DESC",
            "date": "f.created_at DESC, f.id DESC",
            "domain": "f.domain COLLATE NOCASE, f.id DESC",
        }
        ordering = orderings.get(sort, orderings["priority"])
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT f.*, b.name AS brand, b.topic, m.parent_company,
                            b.official_domain, m.ticker, m.market_cap_usd,
                            m.status AS mapping_status, m.finance_source,
                            m.finance_fetched_at, m.last_error AS finance_error
                     FROM findings f JOIN brands b ON b.id=f.brand_id
                     LEFT JOIN company_mappings m ON m.brand_id=b.id
                     WHERE {' AND '.join(clauses)}
                     ORDER BY {ordering}""", params,
            ).fetchall()
            return [self._decode_finding(dict(row)) for row in rows]

    def list_all_finding_groups(self, risk: str = "", review: str = "", q: str = "",
                                sort: str = "priority") -> list[dict]:
        rows = self.list_all_findings(risk=risk, review=review, q=q, sort=sort)
        result = self._group_finding_rows(rows, sort)
        self._attach_company_priorities(result)
        return result

    @staticmethod
    def _company_key(row: dict) -> tuple:
        parent = (row.get("parent_company") or "").strip()
        if parent and row.get("mapping_status") in {"confirmed", "auto_confirmed"}:
            return "company", parent.casefold()
        return "brand", int(row["brand_id"])

    @staticmethod
    def _company_key_text(key: tuple) -> str:
        return f"{key[0]}:{key[1]}"

    def company_priority_by_brand(self) -> dict[int, int]:
        grouped: dict[tuple, dict] = {}
        for row in self.list_all_findings():
            if row["review_status"] == "false_positive":
                continue
            item = grouped.setdefault(
                self._company_key(row), {"brand_ids": set(), "priority": 0},
            )
            item["brand_ids"].add(int(row["brand_id"]))
            item["priority"] = max(item["priority"], int(row["priority_score"]))
        return {
            brand_id: item["priority"]
            for item in grouped.values()
            for brand_id in item["brand_ids"]
        }

    def _attach_company_priorities(self, rows: list[dict]) -> None:
        priorities = self.company_priority_by_brand()
        for row in rows:
            row["company_priority_score"] = priorities.get(
                int(row["brand_id"]), 0,
            )

    def list_company_investigations(
        self, risk: str = "", review: str = "", q: str = "", sort: str = "company",
    ) -> list[dict]:
        all_domains = self.list_all_finding_groups(sort="priority")
        visible_domains = self.list_all_finding_groups(
            risk=risk, review=review, q=q, sort="priority",
        )
        all_by_company: dict[tuple, list[dict]] = {}
        visible_by_company: dict[tuple, list[dict]] = {}
        for row in all_domains:
            all_by_company.setdefault(self._company_key(row), []).append(row)
        for row in visible_domains:
            visible_by_company.setdefault(self._company_key(row), []).append(row)

        outreach_keys = self.outreach_keys()
        companies = []
        for key, domains in visible_by_company.items():
            complete_domains = all_by_company[key]
            representative = max(
                complete_domains,
                key=lambda item: (item["company_priority_score"], item["risk_score"], item["id"]),
            )
            active_domains = [
                item for item in complete_domains if item["review_status"] != "false_positive"
            ] or complete_domains
            highest_risk = max(active_domains, key=lambda item: (item["risk_score"], item["id"]))
            company_name = (
                representative.get("parent_company")
                if key[0] == "company"
                else representative["brand"]
            )
            companies.append({
                "company_key": self._company_key_text(key),
                "anchor_id": min(int(item["brand_id"]) for item in complete_domains),
                "company_name": company_name,
                "brands": sorted({item["brand"] for item in complete_domains}, key=str.casefold),
                "ticker": representative.get("ticker") or "",
                "market_cap_usd": representative.get("market_cap_usd"),
                "priority_score": max(item["company_priority_score"] for item in complete_domains),
                "risk_score": highest_risk["risk_score"],
                "risk_level": highest_risk["risk_level"],
                "domain_count": len(complete_domains),
                "visible_domain_count": len(domains),
                "page_count": sum(item["page_count"] for item in complete_domains),
                "high_domain_count": sum(item["risk_level"] == "high" for item in active_domains),
                "awaiting_review_count": sum(
                    item["review_status"] in {"unreviewed", "investigate"}
                    for item in complete_domains
                ),
                "latest": max(item["created_at"] for item in complete_domains),
                "on_outreach_list": self._company_key_text(key) in outreach_keys,
                "domains": sorted(
                    domains,
                    key=lambda item: (
                        -item["risk_score"],
                        (item.get("registrable_domain") or item["domain"]).casefold(),
                    ),
                ),
            })

        sorters = {
            "company": lambda item: (item["company_name"].casefold(), item["anchor_id"]),
            "priority": lambda item: (-item["priority_score"], item["company_name"].casefold()),
            "risk": lambda item: (-item["risk_score"], item["company_name"].casefold()),
            "date": lambda item: (item["latest"], item["company_name"].casefold()),
        }
        companies.sort(key=sorters.get(sort, sorters["company"]), reverse=sort == "date")
        return companies

    def add_company_outreach(self, company: dict) -> None:
        now = utc_now()
        brand_ids = sorted({int(item["brand_id"]) for item in company["domains"]})
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO company_outreach(
                       company_key, company_name, brand_ids_json, status, created_at, updated_at
                   ) VALUES (?,?,?,'pending',?,?)
                   ON CONFLICT(company_key) DO UPDATE SET
                       company_name=excluded.company_name,
                       brand_ids_json=excluded.brand_ids_json,
                       status='pending',
                       updated_at=excluded.updated_at""",
                (
                    company["company_key"], company["company_name"],
                    json.dumps(brand_ids), now, now,
                ),
            )

    def remove_company_outreach(self, company_key: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM company_outreach WHERE company_key=?", (company_key,),
            )

    def outreach_keys(self) -> set[str]:
        with self.connect() as connection:
            return {
                row["company_key"]
                for row in connection.execute("SELECT company_key FROM company_outreach")
            }

    def outreach_count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM company_outreach").fetchone()[0])

    def list_outreach_companies(self) -> list[dict]:
        current = {
            company["company_key"]: company
            for company in self.list_company_investigations(sort="company")
        }
        with self.connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM company_outreach ORDER BY company_name COLLATE NOCASE"
            ).fetchall()]
        for row in rows:
            row["brand_ids"] = json.loads(row.pop("brand_ids_json") or "[]")
            row["company"] = current.get(row["company_key"])
        return rows

    @staticmethod
    def _group_finding_rows(rows: list[dict], sort: str) -> list[dict]:
        grouped: dict[tuple, list[dict]] = {}
        for row in rows:
            key = (row["brand_id"], row.get("registrable_domain") or row["domain"])
            grouped.setdefault(key, []).append(row)
        result = []
        for members in grouped.values():
            representative = max(
                members,
                key=lambda item: (item["priority_score"], item["risk_score"], -(item.get("rank") or 9999)),
            ).copy()
            representative["page_count"] = len(members)
            representative["scan_count"] = len({item["scan_id"] for item in members})
            representative["related_finding_ids"] = [item["id"] for item in members]
            result.append(representative)
        sorters = {
            "priority": lambda item: (-item["priority_score"], -item["risk_score"], item["id"]),
            "risk": lambda item: (-item["risk_score"], -item["priority_score"], item["id"]),
            "date": lambda item: (item["created_at"], item["id"]),
            "domain": lambda item: ((item.get("registrable_domain") or item["domain"]).casefold(), item["id"]),
        }
        result.sort(key=sorters.get(sort, sorters["priority"]), reverse=sort == "date")
        return result

    def get_finding(self, finding_id: int):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT f.*, b.name AS brand, b.topic, m.parent_company,
                          b.official_domain, m.ticker, m.market_cap_usd,
                          m.status AS mapping_status, m.finance_source,
                          m.finance_fetched_at, m.last_error AS finance_error
                   FROM findings f JOIN brands b ON b.id=f.brand_id
                   LEFT JOIN company_mappings m ON m.brand_id=b.id
                   WHERE f.id=?""", (finding_id,),
            ).fetchone()
            finding = self._decode_finding(dict(row)) if row else None
        if finding:
            finding["company_priority_score"] = self.company_priority_by_brand().get(
                int(finding["brand_id"]), int(finding["priority_score"]),
            )
        return finding

    def related_findings(self, finding_id: int) -> list[dict]:
        finding = self.get_finding(finding_id)
        if not finding:
            return []
        domain = finding.get("registrable_domain") or finding["domain"]
        return [
            row for row in self.list_all_findings()
            if row["id"] != finding_id
            and row["brand_id"] == finding["brand_id"]
            and (row.get("registrable_domain") or row["domain"]) == domain
        ]

    @staticmethod
    def _decode_finding(row: dict) -> dict:
        row["evidence"] = json.loads(row.pop("evidence_json") or "[]")
        official = (row.get("official_domain") or "").strip().lower()
        if official:
            official_host = domain_of(official if "://" in official else f"https://{official}")
            row["official_domain_match"] = (
                registrable_domain(official_host)
                == (row.get("registrable_domain") or registrable_domain(row.get("domain", "")))
            )
        else:
            row["official_domain_match"] = None
        return row

    def update_review(self, finding_id: int, status: str, note: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE findings SET review_status=?, review_note=? WHERE id=?",
                (status, note.strip()[:2000], finding_id),
            )

    def refresh_brand_priorities(self, brand_id: int, market_cap_usd) -> None:
        from fakeshop.scoring import priority_score

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, risk_score FROM findings WHERE brand_id=?", (brand_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE findings SET priority_score=? WHERE id=?",
                    (priority_score(row["risk_score"], market_cap_usd), row["id"]),
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
        self.refresh_brand_priorities(brand_id, defaults["market_cap_usd"])

    def list_mappings(self, q: str = "", status: str = "") -> list[dict]:
        clauses = ["1=1"]
        params = []
        if q:
            clauses.append("(b.name LIKE ? OR m.parent_company LIKE ? OR m.ticker LIKE ?)")
            needle = f"%{q.strip()}%"
            params.extend([needle, needle, needle])
        if status:
            clauses.append("COALESCE(m.status, 'pending')=?")
            params.append(status)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT b.id AS brand_id, b.name AS brand, b.topic, b.official_domain, m.*
                   FROM brands b LEFT JOIN company_mappings m ON m.brand_id=b.id
                   WHERE {' AND '.join(clauses)}
                   ORDER BY CASE WHEN m.status='needs_review' THEN 0 ELSE 1 END, b.name"""
                , params,
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["candidates"] = json.loads(item.get("candidates_json") or "[]")
                result.append(item)
            return result

    def mapping_stats(self) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status IN ('confirmed','auto_confirmed') THEN 1 ELSE 0 END) AS confirmed,
                          SUM(CASE WHEN status='needs_review' THEN 1 ELSE 0 END) AS needs_review,
                          SUM(CASE WHEN status='no_match' THEN 1 ELSE 0 END) AS no_match
                   FROM company_mappings"""
            ).fetchone()
            return {key: (row[key] or 0) for key in row.keys()}

    def latest_finance_update(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(finance_fetched_at) AS updated FROM company_mappings"
            ).fetchone()
            return row["updated"] or "Not updated yet"

    def finance_status(self) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT MAX(finance_fetched_at) AS attempted,
                          MAX(CASE WHEN market_cap_usd IS NOT NULL THEN finance_fetched_at ELSE '' END) AS successful
                   FROM company_mappings"""
            ).fetchone()
            return {
                "attempted": row["attempted"] or "Not updated yet",
                "successful": row["successful"] or "",
            }

    def active_scans(self, limit: int = 5) -> list[dict]:
        return [scan for scan in self.list_scans(limit=50) if scan["status"] in {"queued", "running"}][:limit]

    def urgent_companies(self, limit: int = 5) -> list[dict]:
        return self.list_company_investigations(review="open", sort="priority")[:limit]

    def dashboard_stats(self) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM scan_runs) AS scans,
                    (SELECT COUNT(DISTINCT COALESCE(NULLIF(registrable_domain,''), domain)) FROM findings) AS findings,
                    (SELECT COUNT(DISTINCT brand_id) FROM findings WHERE risk_level='high') AS high_companies,
                    (SELECT COUNT(DISTINCT brand_id) FROM findings WHERE review_status IN ('unreviewed','investigate')) AS pending_review_companies,
                    (SELECT COUNT(*) FROM findings WHERE review_status='confirmed') AS confirmed"""
            ).fetchone()
            return dict(row)
