from pathlib import Path

import fakeshop.jobs as jobs
from fakeshop.db import Repository


class FakeCapturer:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeEngine:
    def __init__(self, provider):
        self.provider = provider

    def scan_brand(self, brand, **kwargs):
        return [{
            "rank": 1,
            "url": f"https://{brand.lower()}-outlet.shop",
            "final_url": f"https://{brand.lower()}-outlet.shop",
            "page_text": "What Are The Costumers Say",
            "search_snippet": "",
            "domain": f"{brand.lower()}-outlet.shop",
            "domain_age_days": 10,
        }]


def test_worker_completes_persistent_job(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(jobs, "Capturer", FakeCapturer)
    monkeypatch.setattr(jobs, "ScanEngine", FakeEngine)
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=3, source_name="Nike",
        targets=[{"brand": "Nike"}],
    )
    worker = jobs.ScanWorker(
        repository, tmp_path, isolate_targets=False, inter_target_delay=0,
    )
    monkeypatch.setattr(
        worker.finance, "enrich_brand",
        lambda repo, target: {"market_cap_usd": 200_000_000_000},
    )
    scan = repository.claim_next_scan()
    worker._process_scan(scan)
    assert repository.get_scan(scan_id)["status"] == "completed"
    finding = repository.list_findings(scan_id)[0]
    assert finding["risk_score"] == 80
    assert finding["risk_level"] == "high"
    assert finding["priority_score"] == 85


def test_timed_out_target_does_not_stop_batch(monkeypatch, tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=1, source_name="batch.csv",
        targets=[{"brand": "Stuck"}, {"brand": "Healthy"}],
    )
    worker = jobs.ScanWorker(
        repository, tmp_path, isolate_targets=False, inter_target_delay=0,
    )
    outcomes = iter([
        ("timeout", "safety deadline"),
        ("timeout", "safety deadline"),
        ("completed", ""),
    ])
    monkeypatch.setattr(worker, "_run_target", lambda *args: next(outcomes))

    worker._process_scan(repository.claim_next_scan())

    scan = repository.get_scan(scan_id)
    targets = repository.list_scan_targets(scan_id)
    assert scan["status"] == "completed"
    assert scan["progress_current"] == 2
    assert scan["current_target"] == ""
    assert targets[0]["status"] == "failed"
    assert targets[0]["attempt_count"] == 2
    assert targets[1]["status"] == "completed"
    assert targets[1]["attempt_count"] == 1
