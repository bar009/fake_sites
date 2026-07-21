import re
from pathlib import Path

from fastapi.testclient import TestClient

import fakeshop.web as web


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def test_dashboard_and_brand_scan(tmp_path: Path):
    app = web.create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert 'lang="en" dir="ltr"' in dashboard.text
        assert "Yahoo Finance through yfinance" in dashboard.text

        form = client.get("/scans/new")
        response = client.post(
            "/scans/brand",
            data={"csrf_token": csrf_from(form), "brand": "Nike", "provider": "ddgs", "top_n": 3},
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Nike" in detail.text
        assert "Queued" in detail.text


def test_csv_upload_and_url_validation(monkeypatch, tmp_path: Path):
    app = web.create_app(tmp_path, start_worker=False)
    monkeypatch.setattr(web, "validate_public_url", lambda value: value)
    with TestClient(app) as client:
        form = client.get("/scans/new")
        token = csrf_from(form)
        upload = client.post(
            "/scans/csv",
            data={"csrf_token": token, "provider": "ddgs", "top_n": 2},
            files={"file": ("brands.csv", b"brand,topic\nNike,sportswear\n", "text/csv")},
            follow_redirects=False,
        )
        assert upload.status_code == 303

        direct = client.post(
            "/scans/url",
            data={"csrf_token": token, "brand": "Nike", "url": "https://nike-outlet.shop"},
            follow_redirects=False,
        )
        assert direct.status_code == 303


def test_local_hypermedia_assets_and_filter_fallback(tmp_path: Path):
    app = web.create_app(tmp_path, start_worker=False)
    repository = app.state.repository
    scan_id = repository.create_scan(
        kind="brand", provider="ddgs", top_n=1, source_name="Example",
        targets=[{"brand": "Example", "official_domain": "example.com"}],
    )
    target = repository.pending_targets(scan_id)[0]
    finding_id = repository.add_finding(
        scan_id=scan_id, brand_id=target["brand_id"],
        row={"url": "https://example-sale.shop/path", "domain": "example-sale.shop",
             "registrable_domain": "example-sale.shop", "query": "site:.shop query",
             "search_title": "Search title", "search_snippet": "Search snippet",
             "page_title": "Page title", "final_url": "https://redirect.shop/"},
        assessment={"score": 80, "level": "high", "evidence": []}, priority=80,
    )
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert "/static/vendor/htmx.min.js" in dashboard.text
        assert "unpkg.com" not in dashboard.text
        assert "https://unpkg.com" not in dashboard.headers["content-security-policy"]
        filtered = client.get(f"/scans/{scan_id}?risk=high&q=example")
        assert filtered.status_code == 200 and "example-sale.shop" in filtered.text
        partial = client.get(f"/scans/{scan_id}/findings?risk=low", headers={"HX-Request": "true"})
        assert "No matching findings" in partial.text
        detail = client.get(f"/findings/{finding_id}")
        assert "site:.shop query" in detail.text
        assert "Search snippet" in detail.text
        assert "example.com" in detail.text
        assert '<a href="https://example-sale.shop' not in detail.text
        exported = client.get(f"/scans/{scan_id}/export/html")
        assert "<html lang='en' dir='ltr'>" in exported.text
        assert "Scan #" in exported.text


def test_user_interface_contains_no_fixed_hebrew_copy():
    hebrew = re.compile(r"[\u0590-\u05ff]")
    files = list((web.PACKAGE_DIR / "templates").rglob("*.html")) + [
        web.PACKAGE_DIR / "static" / "app.js",
        web.PACKAGE_DIR / "static" / "vendor" / "htmx.min.js",
    ]
    assert not {
        str(path): hebrew.findall(path.read_text(encoding="utf-8"))
        for path in files if hebrew.search(path.read_text(encoding="utf-8"))
    }
