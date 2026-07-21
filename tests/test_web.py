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
    screenshot = tmp_path / "scans" / str(scan_id) / "example.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"not-a-real-png")
    finding_id = repository.add_finding(
        scan_id=scan_id, brand_id=target["brand_id"],
        row={"url": "https://example-sale.shop/path", "domain": "example-sale.shop",
             "registrable_domain": "example-sale.shop", "query": "site:.shop query",
             "search_title": "Search title", "search_snippet": "Search snippet",
             "page_title": "Page title", "final_url": "https://redirect.shop/",
             "screenshot_path": str(screenshot)},
        assessment={"score": 80, "level": "high", "evidence": [
            {"code": "template_phrase", "label": "Storefront template fingerprint",
             "detail": "Phrase found", "points": 40},
        ]}, priority=80,
    )
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert "/static/vendor/htmx.min.js" in dashboard.text
        assert '/static/favicon.svg' in dashboard.text
        favicon = client.get("/favicon.ico")
        assert favicon.status_code == 200
        assert favicon.headers["content-type"].startswith("image/svg+xml")
        assert "unpkg.com" not in dashboard.text
        assert "https://unpkg.com" not in dashboard.headers["content-security-policy"]
        assert "High-risk companies" in dashboard.text
        assert "Awaiting review (companies)" in dashboard.text
        investigations = client.get("/findings")
        assert investigations.status_code == 200
        assert "Priority belongs to the company; risk belongs to the website" in investigations.text
        assert "Company priority" in investigations.text
        assert f'href="/findings/{finding_id}"' in investigations.text
        assert "https://example-sale.shop/path" in investigations.text
        assert f'src="/findings/{finding_id}/screenshot"' in investigations.text
        global_partial = client.get("/findings/list?risk=low", headers={"HX-Request": "true"})
        assert "No matching companies" in global_partial.text
        filtered = client.get(f"/scans/{scan_id}?risk=high&q=example")
        assert filtered.status_code == 200 and "example-sale.shop" in filtered.text
        partial = client.get(f"/scans/{scan_id}/findings?risk=low", headers={"HX-Request": "true"})
        assert "No matching findings" in partial.text
        detail = client.get(f"/findings/{finding_id}")
        assert "site:.shop query" in detail.text
        assert "Search snippet" in detail.text
        assert "example.com" in detail.text
        assert "Detected indicators" in detail.text
        assert "Copy case summary" in detail.text
        assert "Company priority" in detail.text
        assert "Potential brand impersonation case" in detail.text
        assert '<a href="https://example-sale.shop' not in detail.text
        mappings = client.get("/mappings")
        assert "This is not the suspicious website list" in mappings.text
        exported = client.get(f"/scans/{scan_id}/export/html")
        assert "<html lang='en' dir='ltr'>" in exported.text
        assert "Scan #" in exported.text


def test_windows_readme_requires_no_terminal_knowledge():
    root = web.PROJECT_ROOT
    readme = (root / "README.md").read_text(encoding="utf-8")
    launcher = (root / "start_windows.bat").read_text(encoding="utf-8")
    assert "Add python.exe to PATH" in readme
    assert "Extract All" in readme
    assert "Double-click `start_windows.bat`" in readme
    assert "Open in Terminal" not in readme
    assert "-m fakeshop.web" in launcher
    assert "playwright install chromium" in launcher
    assert (web.PACKAGE_DIR / "static" / "favicon.svg").is_file()


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
