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
        assert 'dir="rtl"' in dashboard.text
        assert "Yahoo Finance דרך yfinance" in dashboard.text

        form = client.get("/scans/new")
        response = client.post(
            "/scans/brand",
            data={"csrf_token": csrf_from(form), "brand": "Nike", "provider": "ddgs", "top_n": 3},
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Nike" in detail.text
        assert "ממתינה בתור" in detail.text


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
