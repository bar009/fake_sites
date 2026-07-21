"""Persistent scan queue with automatic recovery and per-target isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fakeshop.capture import Capturer
from fakeshop.db import Repository
from fakeshop.engine import ScanEngine
from fakeshop.finance import FinanceService
from fakeshop.scoring import assess_risk, priority_score


HEARTBEAT_SECONDS = 5.0
POLL_PROCESS_SECONDS = 0.5
DEFAULT_DELAY_SECONDS = 4.0
DEFAULT_TIMEOUT_BASE_SECONDS = 120
DEFAULT_TIMEOUT_PER_RESULT_SECONDS = 45


class ScanWorker:
    def __init__(
        self,
        repository: Repository,
        data_dir: Path,
        poll_seconds: float = 1.0,
        *,
        isolate_targets: bool = True,
        inter_target_delay: float = DEFAULT_DELAY_SECONDS,
        timeout_base_seconds: int = DEFAULT_TIMEOUT_BASE_SECONDS,
        timeout_per_result_seconds: int = DEFAULT_TIMEOUT_PER_RESULT_SECONDS,
    ):
        self.repository = repository
        self.data_dir = Path(data_dir)
        self.poll_seconds = poll_seconds
        self.isolate_targets = isolate_targets
        self.inter_target_delay = inter_target_delay
        self.timeout_base_seconds = timeout_base_seconds
        self.timeout_per_result_seconds = timeout_per_result_seconds
        self.finance = FinanceService()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # A process restart is a recoverable pause, not a terminal scan state.
        self.repository.recover_interrupted()
        self._thread = threading.Thread(target=self._run, name="fakeshop-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=15)

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
        screenshot_dir = self.data_dir / "scans" / str(scan_id) / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        targets = self.repository.pending_targets(scan_id)
        try:
            for index, target in enumerate(targets):
                if self._stop.is_set():
                    return
                if self.repository.should_cancel(scan_id):
                    self.repository.finish_scan(scan_id, "cancelled")
                    return
                if index and self.inter_target_delay and self._stop.wait(self.inter_target_delay):
                    return

                self.repository.heartbeat_scan(scan_id, target["brand"])
                outcome = "failed"
                error = ""
                for attempt in range(2):
                    self.repository.set_target_status(target["id"], "running")
                    outcome, error = self._run_target(scan, target, screenshot_dir)
                    if outcome == "completed":
                        self.repository.set_target_status(target["id"], "completed")
                        break
                    if outcome == "cancelled":
                        self.repository.set_target_status(target["id"], "pending")
                        if self._stop.is_set():
                            return
                        self.repository.finish_scan(scan_id, "cancelled")
                        return
                    # A hard timeout or child crash gets one clean-process retry.
                    if outcome in {"timeout", "crashed"} and attempt == 0:
                        self.repository.set_target_status(target["id"], "pending")
                        self.repository.heartbeat_scan(scan_id, f"Retrying {target['brand']}")
                        continue
                    self.repository.set_target_status(target["id"], "failed", error[:1000])
                    break
                self.repository.advance_scan(scan_id)
                self.repository.heartbeat_scan(scan_id)
            self.repository.finish_scan(scan_id, "completed")
        except Exception as exc:  # the queue records unexpected worker failures
            self.repository.finish_scan(scan_id, "failed", str(exc)[:1000])

    def _run_target(self, scan: dict, target: dict, screenshot_dir: Path) -> tuple[str, str]:
        if not self.isolate_targets:
            try:
                self._process_target(scan, target, screenshot_dir)
                return "completed", ""
            except Exception as exc:
                return "failed", str(exc)
        try:
            return self._run_target_subprocess(scan, target, screenshot_dir)
        except Exception as exc:
            return "crashed", f"Could not run the isolated target process: {type(exc).__name__}: {exc}"

    def _target_timeout(self, scan: dict) -> int:
        configured = os.environ.get("FAKESHOP_TARGET_TIMEOUT_SECONDS", "").strip()
        if configured:
            return max(30, int(configured))
        return self.timeout_base_seconds + (int(scan.get("top_n") or 1) * self.timeout_per_result_seconds)

    def _run_target_subprocess(
        self, scan: dict, target: dict, screenshot_dir: Path,
    ) -> tuple[str, str]:
        job_dir = self.data_dir / "worker"
        job_dir.mkdir(parents=True, exist_ok=True)
        token = f"scan-{scan['id']}-target-{target['id']}-{int(time.time() * 1000)}"
        payload_path = job_dir / f"{token}.json"
        result_path = job_dir / f"{token}.result.json"
        log_path = job_dir / f"{token}.log"
        payload_path.write_text(json.dumps({
            "db_path": str(self.repository.db_path),
            "data_dir": str(self.data_dir),
            "screenshot_dir": str(screenshot_dir),
            "scan": scan,
            "target": target,
            "result_path": str(result_path),
        }, ensure_ascii=False, default=str), encoding="utf-8")

        timeout_seconds = self._target_timeout(scan)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        project_root = Path(__file__).resolve().parent.parent
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", "fakeshop.target_runner", str(payload_path)],
                cwd=project_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            started = time.monotonic()
            next_heartbeat = started
            while process.poll() is None:
                now = time.monotonic()
                if self._stop.is_set() or self.repository.should_cancel(scan["id"]):
                    self._terminate_process(process)
                    payload_path.unlink(missing_ok=True)
                    result_path.unlink(missing_ok=True)
                    return "cancelled", "Target process stopped"
                if now - started >= timeout_seconds:
                    self._terminate_process(process)
                    payload_path.unlink(missing_ok=True)
                    result_path.unlink(missing_ok=True)
                    return (
                        "timeout",
                        f"Target exceeded the {timeout_seconds}-second safety deadline; the batch continued automatically.",
                    )
                if now >= next_heartbeat:
                    self.repository.heartbeat_scan(scan["id"], target["brand"])
                    next_heartbeat = now + HEARTBEAT_SECONDS
                self._stop.wait(POLL_PROCESS_SECONDS)

        try:
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("ok"):
                    if log_path.exists() and not log_path.stat().st_size:
                        log_path.unlink(missing_ok=True)
                    return "completed", ""
                return "failed", str(result.get("error") or "Target process failed")
            tail = ""
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            return "crashed", tail or f"Target process exited with code {process.returncode}"
        finally:
            payload_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _process_target(self, scan: dict, target: dict, screenshot_dir: Path) -> None:
        engine = ScanEngine(scan["provider"])
        with Capturer() as capturer:
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
                    scan_id=scan["id"], brand_id=target["brand_id"], row=row,
                    assessment=assessment, priority=priority,
                )
