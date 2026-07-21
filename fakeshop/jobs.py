"""Persistent single-worker scan queue for the local application."""

from __future__ import annotations

import threading
from pathlib import Path

from fakeshop.capture import Capturer
from fakeshop.db import Repository
from fakeshop.engine import ScanEngine
from fakeshop.finance import FinanceService
from fakeshop.scoring import assess_risk, priority_score


class ScanWorker:
    def __init__(self, repository: Repository, data_dir: Path, poll_seconds: float = 1.0):
        self.repository = repository
        self.data_dir = Path(data_dir)
        self.poll_seconds = poll_seconds
        self.finance = FinanceService()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.repository.recover_interrupted()
        self._thread = threading.Thread(target=self._run, name="fakeshop-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            scan = self.repository.claim_next_scan()
            if scan:
                self._process_scan(scan)
                continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _process_scan(self, scan: dict) -> None:
        scan_id = scan["id"]
        engine = ScanEngine(scan["provider"])
        screenshot_dir = self.data_dir / "scans" / str(scan_id) / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            with Capturer() as capturer:
                for target in self.repository.pending_targets(scan_id):
                    if self.repository.should_cancel(scan_id) or self._stop.is_set():
                        self.repository.finish_scan(scan_id, "cancelled")
                        return
                    self.repository.set_target_status(target["id"], "running")
                    try:
                        mapping = self.finance.enrich_brand(self.repository, target)
                        if target["input_url"]:
                            rows = engine.scan_url(
                                target["brand"], target["input_url"],
                                screenshot_dir=screenshot_dir, capturer=capturer,
                            )
                        else:
                            rows = engine.scan_brand(
                                target["brand"], top=scan["top_n"],
                                screenshot_dir=screenshot_dir, capturer=capturer,
                            )
                        for row in rows:
                            assessment = assess_risk(
                                brand=target["brand"], url=row["url"],
                                final_url=row.get("final_url", ""),
                                page_text=row.get("page_text", ""),
                                search_snippet=row.get("search_snippet", ""),
                                domain_age_days=row.get("domain_age_days"),
                            )
                            priority = priority_score(
                                assessment["score"], mapping.get("market_cap_usd") if mapping else None,
                            )
                            self.repository.add_finding(
                                scan_id=scan_id, brand_id=target["brand_id"], row=row,
                                assessment=assessment, priority=priority,
                            )
                        self.repository.set_target_status(target["id"], "completed")
                    except Exception as exc:  # one target cannot kill a batch
                        self.repository.set_target_status(target["id"], "failed", str(exc)[:1000])
                    finally:
                        self.repository.advance_scan(scan_id)
            self.repository.finish_scan(scan_id, "completed")
        except Exception as exc:
            self.repository.finish_scan(scan_id, "failed", str(exc)[:1000])
