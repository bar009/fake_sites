import json
import sqlite3
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


def test_interrupted_scan_is_automatically_requeued(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=3, source_name="A",
        targets=[{"brand": "A"}],
    )
    repository.claim_next_scan()
    target = repository.pending_targets(scan_id)[0]
    repository.set_target_status(target["id"], "running")
    repository.heartbeat_scan(scan_id, "A")
    repository.recover_interrupted()
    recovered = repository.get_scan(scan_id)
    assert recovered["status"] == "queued"
    assert recovered["current_target"] == ""
    assert "Automatically resumed" in recovered["recovery_note"]
    assert repository.pending_targets(scan_id)[0]["status"] == "pending"
    assert repository.claim_next_scan()["id"] == scan_id
    assert repository.get_scan(scan_id)["status"] == "running"


def test_finding_metadata_grouping_targets_and_priority_refresh(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=3, source_name="Duck Camp",
        targets=[{"brand": "DUCKCAMP", "official_domain": "duckcamp.com"}],
    )
    target = repository.pending_targets(scan_id)[0]
    for rank, url in enumerate(("https://deal.example.shop/", "https://example.shop/sale"), 1):
        repository.add_finding(
            scan_id=scan_id, brand_id=target["brand_id"],
            row={"rank": rank, "url": url, "domain": url.split("/")[2],
                 "registrable_domain": "example.shop", "query": "site:.shop query",
                 "search_title": f"Hit {rank}", "search_snippet": "snippet",
                 "final_url": url, "page_title": f"Page {rank}", "note": "captured"},
            assessment={"score": 80, "level": "high", "evidence": []}, priority=80,
        )
    repository.set_target_status(target["id"], "completed")

    groups = repository.list_finding_groups(scan_id)
    assert len(groups) == 1 and groups[0]["page_count"] == 2
    finding = repository.get_finding(groups[0]["id"])
    assert finding["search_query"] == "site:.shop query"
    assert finding["capture_note"] == "captured"
    assert finding["official_domain"] == "duckcamp.com"
    assert repository.list_scan_targets(scan_id)[0]["domain_count"] == 1

    repository.save_mapping(
        target["brand_id"], parent_company="Duck Camp", ticker="DUCK",
        status="confirmed", market_cap_usd=150_000_000_000,
        finance_source="yfinance", finance_fetched_at="2026-07-22T00:00:00Z",
    )
    assert {row["priority_score"] for row in repository.list_findings(scan_id)} == {85}


def test_dashboard_counts_domains_companies_and_scan_history(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="csv", provider="ddgs", top_n=3, source_name="brands.csv",
        targets=[{"brand": "Alpha"}, {"brand": "Beta"}],
    )
    targets = repository.pending_targets(scan_id)
    alpha, beta = targets
    for domain in ("alpha-sale.shop", "alpha-outlet.shop"):
        repository.add_finding(
            scan_id=scan_id, brand_id=alpha["brand_id"],
            row={"url": f"https://{domain}", "domain": domain,
                 "registrable_domain": domain},
            assessment={"score": 80, "level": "high", "evidence": []}, priority=80,
        )
    repository.add_finding(
        scan_id=scan_id, brand_id=beta["brand_id"],
        row={"url": "https://beta-shop.shop", "domain": "beta-shop.shop",
             "registrable_domain": "beta-shop.shop"},
        assessment={"score": 45, "level": "medium", "evidence": []}, priority=45,
    )

    stats = repository.dashboard_stats()
    assert stats["findings"] == 3
    assert stats["high_companies"] == 1
    assert stats["pending_review_companies"] == 2
    assert stats["scans"] == 1
    assert len(repository.list_all_finding_groups(review="open")) == 3


def test_company_investigations_are_alphabetical_and_share_priority(tmp_path: Path):
    repository = Repository(tmp_path / "app.db")
    scan_id = repository.create_scan(
        kind="csv", provider="ddgs", top_n=2, source_name="brands.csv",
        targets=[{"brand": "Shop Brand"}, {"brand": "Outlet Brand"}, {"brand": "Beta Corp"}],
    )
    targets = repository.pending_targets(scan_id)
    finding_ids = []
    for target, score, domain in zip(
        targets, (60, 90, 45),
        ("shop-brand.shop", "outlet-brand.shop", "beta-corp.shop"),
    ):
        finding_ids.append(repository.add_finding(
            scan_id=scan_id, brand_id=target["brand_id"],
            row={"url": f"https://{domain}", "domain": domain,
                 "registrable_domain": domain},
            assessment={
                "score": score,
                "level": "high" if score >= 60 else "medium",
                "evidence": [],
            },
            priority=score,
        ))
    for target in targets[:2]:
        repository.save_mapping(
            target["brand_id"], parent_company="Acme Group", status="confirmed",
            market_cap_usd=None,
        )

    companies = repository.list_company_investigations()
    assert [item["company_name"] for item in companies] == ["Acme Group", "Beta Corp"]
    assert [item["directory_letter"] for item in companies] == ["A", "B"]
    assert companies[0]["brands"] == ["Outlet Brand", "Shop Brand"]
    assert companies[0]["priority_score"] == 90
    assert {item["company_priority_score"] for item in companies[0]["domains"]} == {90}

    repository.update_review(finding_ids[1], "false_positive", "Not an impersonation")
    companies = repository.list_company_investigations()
    assert companies[0]["priority_score"] == 60


def test_company_outreach_list_is_persistent(tmp_path: Path):
    db_path = tmp_path / "app.db"
    repository = Repository(db_path)
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=1, source_name="Example",
        targets=[{"brand": "Example"}],
    )
    target = repository.pending_targets(scan_id)[0]
    repository.add_finding(
        scan_id=scan_id, brand_id=target["brand_id"],
        row={"url": "https://example-sale.shop", "domain": "example-sale.shop",
             "registrable_domain": "example-sale.shop"},
        assessment={"score": 80, "level": "high", "evidence": []}, priority=80,
    )
    company = repository.list_company_investigations()[0]
    repository.add_company_outreach(company)

    reopened = Repository(db_path)
    assert reopened.outreach_count() == 1
    assert reopened.list_company_investigations()[0]["on_outreach_list"] is True
    assert reopened.list_outreach_companies()[0]["company_name"] == "Example"

    reopened.remove_company_outreach(company["company_key"])
    assert reopened.outreach_count() == 0


def test_migration_merges_duckcamp_aliases_without_losing_references(tmp_path: Path):
    db_path = tmp_path / "app.db"
    repository = Repository(db_path)
    first = repository.upsert_brand({"brand": "DUCK CAMP", "topic": "outdoors"})
    with sqlite3.connect(db_path) as connection:
        second = connection.execute(
            "INSERT INTO brands(name, normalized_name, official_domain) VALUES (?,?,?)",
            ("DUCKCAMP", "duckcamp-legacy", "duckcamp.com"),
        ).lastrowid
        scan_id = connection.execute(
            "INSERT INTO scan_runs(kind,status,created_at) VALUES ('brand','completed','now')"
        ).lastrowid
        target_id = connection.execute(
            "INSERT INTO scan_targets(scan_id,brand_id,status) VALUES (?,?,'completed')",
            (scan_id, second),
        ).lastrowid
        finding_id = connection.execute(
            "INSERT INTO findings(scan_id,brand_id,url,domain,created_at) VALUES (?,?,?,?,?)",
            (scan_id, second, "https://duckcamp-sale.shop", "duckcamp-sale.shop", "now"),
        ).lastrowid
        connection.execute(
            "INSERT INTO company_mappings(brand_id,parent_company,ticker,status,candidates_json) VALUES (?,?,?,?,?)",
            (second, "Duck Camp", "DUCK", "confirmed", json.dumps([{"ticker": "DUCK"}])),
        )
        connection.execute("PRAGMA user_version=1")

    migrated = Repository(db_path)
    with migrated.connect() as connection:
        brands = connection.execute("SELECT * FROM brands").fetchall()
        assert len(brands) == 1
        assert brands[0]["id"] == first and brands[0]["name"] == "Duck Camp"
        assert brands[0]["official_domain"] == "duckcamp.com"
        assert connection.execute("SELECT brand_id FROM scan_targets WHERE id=?", (target_id,)).fetchone()[0] == first
        assert connection.execute("SELECT brand_id FROM findings WHERE id=?", (finding_id,)).fetchone()[0] == first
        assert connection.execute("SELECT status FROM company_mappings WHERE brand_id=?", (first,)).fetchone()[0] == "confirmed"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert list((tmp_path / "backups").glob("app-v1-*.db"))


def test_v3_migration_translates_system_evidence_but_preserves_analyst_text(tmp_path: Path):
    db_path = tmp_path / "app.db"
    repository = Repository(db_path)
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=1, source_name="Example",
        targets=[{"brand": "Example"}],
    )
    target = repository.pending_targets(scan_id)[0]
    finding_id = repository.add_finding(
        scan_id=scan_id, brand_id=target["brand_id"],
        row={"url": "https://example-outlet.shop", "domain": "example-outlet.shop",
             "domain_age_days": 12, "final_url": "https://other.shop"},
        assessment={"score": 50, "level": "medium", "evidence": [
            {"code": "young_domain", "label": "דומיין חדש", "points": 25,
             "detail": "הדומיין נרשם לפני 12 ימים"},
            {"code": "custom", "label": "טקסט היסטורי", "points": 0,
             "detail": "נשמר ללא שינוי"},
        ]},
        priority=50,
    )
    repository.update_review(finding_id, "investigate", "הערת אנליסט נשמרת")
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version=2")

    migrated = Repository(db_path)
    finding = migrated.get_finding(finding_id)
    assert finding["evidence"][0] == {
        "code": "young_domain", "label": "Newly registered domain", "points": 25,
        "detail": "The domain was registered 12 days ago",
    }
    assert finding["evidence"][1]["label"] == "טקסט היסטורי"
    assert finding["review_note"] == "הערת אנליסט נשמרת"
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert list((tmp_path / "backups").glob("app-v2-*.db"))
