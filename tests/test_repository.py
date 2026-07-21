from pathlib import Path

from fakeshop.db import Repository


def test_scan_lifecycle_and_review(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=3, source_name="Nike",
        targets=[{"brand": "Nike", "topic": "sportswear"}],
    )
    scan = repository.claim_next_scan()
    assert scan["id"] == scan_id
    target = repository.pending_targets(scan_id)[0]
    repository.set_target_status(target["id"], "running")
    finding_id = repository.add_finding(
        scan_id=scan_id,
        brand_id=target["brand_id"],
        row={"rank": 1, "url": "https://nike-outlet.shop", "domain": "nike-outlet.shop"},
        assessment={"score": 55, "level": "medium", "evidence": [{"code": "x"}]},
        priority=60,
    )
    repository.set_target_status(target["id"], "completed")
    repository.advance_scan(scan_id)
    repository.finish_scan(scan_id)

    assert repository.get_scan(scan_id)["status"] == "completed"
    assert repository.list_findings(scan_id)[0]["evidence"] == [{"code": "x"}]
    repository.update_review(finding_id, "confirmed", "בדיקה ידנית")
    assert repository.get_finding(finding_id)["review_status"] == "confirmed"


def test_interrupted_scan_can_resume(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=3, source_name="A",
        targets=[{"brand": "A"}],
    )
    repository.claim_next_scan()
    repository.recover_interrupted()
    assert repository.get_scan(scan_id)["status"] == "interrupted"
    assert repository.resume_scan(scan_id)
    assert repository.get_scan(scan_id)["status"] == "queued"
