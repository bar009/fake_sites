"""Local English FastAPI interface for Fake Shop Checker."""

from __future__ import annotations

import csv
import html
import io
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from dotenv import load_dotenv

from fakeshop.db import Repository
from fakeshop.finance import FinanceService
from fakeshop.jobs import ScanWorker
from fakeshop.security import UnsafeUrlError, validate_public_url


PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")
REVIEW_STATUSES = {"unreviewed", "confirmed", "false_positive", "investigate"}
ACTIVE_STATUSES = {"queued", "running"}


def load_csv_rows(payload: bytes) -> list[dict]:
    if len(payload) > 1_000_000:
        raise ValueError("The CSV file is too large; the limit is 1 MB")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The CSV file must use UTF-8 encoding") from exc
    reader = csv.DictReader(io.StringIO(text))
    fields = {str(field).strip().lower() for field in (reader.fieldnames or [])}
    if "brand" not in fields:
        raise ValueError("The CSV file must include a brand column")
    rows = []
    for raw in reader:
        item = {str(key or "").strip().lower(): str(value or "").strip() for key, value in raw.items()}
        if item.get("brand"):
            rows.append({
                "brand": item["brand"][:200],
                "topic": item.get("topic", "")[:200],
                "parent_company": item.get("parent_company", "")[:200],
                "ticker": item.get("ticker", "")[:40],
                "official_domain": item.get("official_domain", "")[:255],
            })
    if not rows:
        raise ValueError("No brands were found in the file")
    if len(rows) > 2_000:
        raise ValueError("A scan can include up to 2,000 brands")
    return rows


def format_money(value) -> str:
    if value in (None, ""):
        return "Not available"
    value = float(value)
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def create_app(data_dir: Path | None = None, *, start_worker: bool = True) -> FastAPI:
    configured_data_dir = os.environ.get("FAKESHOP_DATA_DIR", "").strip()
    data_dir = Path(data_dir or configured_data_dir or PROJECT_ROOT / "data").resolve()
    repository = Repository(data_dir / "fakeshop.db")
    worker = ScanWorker(repository, data_dir)
    csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Fake Shop Checker", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.repository = repository
    app.state.worker = worker
    app.state.data_dir = data_dir
    app.state.csrf_token = csrf_token

    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters["money"] = format_money
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        )
        return response

    def context(request: Request, **values):
        return {
            "request": request,
            "csrf_token": csrf_token,
            "finance_status": repository.finance_status(),
            "outreach_count": repository.outreach_count(),
            "current_path": request.url.path,
            **values,
        }

    def check_csrf(value: str) -> None:
        if not secrets.compare_digest(value, csrf_token):
            raise HTTPException(403, "Invalid form submission")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/static/"):
            return Response(status_code=exc.status_code)
        return templates.TemplateResponse(
            request, "error.html",
            context(request, status_code=exc.status_code, message=str(exc.detail)),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return templates.TemplateResponse(
            request, "error.html",
            context(request, status_code=400,
                    message="Required form details are missing or invalid"),
            status_code=400,
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request, "dashboard.html",
            context(
                request, scans=repository.list_scans(), stats=repository.dashboard_stats(),
                active_scans=repository.active_scans(), urgent_companies=repository.urgent_companies(),
            ),
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(PACKAGE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/scans/new", response_class=HTMLResponse)
    def new_scan(request: Request):
        return templates.TemplateResponse(request, "new_scan.html", context(request))

    @app.post("/scans/csv")
    async def create_csv_scan(
        file: UploadFile = File(...), provider: str = Form("ddgs"), top_n: int = Form(3),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        if provider not in {"ddgs", "brave"} or not 1 <= top_n <= 10:
            raise HTTPException(400, "The scan settings are invalid")
        try:
            rows = load_csv_rows(await file.read())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        scan_id = repository.create_scan(
            kind="csv", targets=rows, provider=provider, top_n=top_n,
            source_name=(file.filename or "upload.csv")[:255],
        )
        worker.wake()
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)

    @app.post("/scans/brand")
    def create_brand_scan(
        brand: str = Form(...), topic: str = Form(""), provider: str = Form("ddgs"),
        top_n: int = Form(3), csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        brand = brand.strip()
        if not brand or len(brand) > 200 or provider not in {"ddgs", "brave"} or not 1 <= top_n <= 10:
            raise HTTPException(400, "The scan details are invalid")
        scan_id = repository.create_scan(
            kind="brand", targets=[{"brand": brand, "topic": topic[:200]}],
            provider=provider, top_n=top_n, source_name=brand,
        )
        worker.wake()
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)

    @app.post("/scans/url")
    def create_url_scan(
        brand: str = Form(...), url: str = Form(...), topic: str = Form(""),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        brand = brand.strip()
        if not brand or len(brand) > 200:
            raise HTTPException(400, "Enter a valid brand name")
        try:
            url = validate_public_url(url)
        except UnsafeUrlError as exc:
            raise HTTPException(400, str(exc)) from exc
        scan_id = repository.create_scan(
            kind="url", targets=[{"brand": brand, "topic": topic[:200], "url": url}],
            provider="ddgs", top_n=1, source_name=url[:255],
        )
        worker.wake()
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)

    @app.get("/scans/{scan_id}", response_class=HTMLResponse)
    def scan_detail(request: Request, scan_id: int, risk: str = "", review: str = "",
                    q: str = "", sort: str = "priority"):
        scan = repository.get_scan(scan_id)
        if not scan:
            raise HTTPException(404)
        findings = repository.list_finding_groups(
            scan_id, risk=risk, review=review, q=q, sort=sort,
        )
        raw_findings = repository.list_findings(scan_id)
        return templates.TemplateResponse(
            request, "scan_detail.html",
            context(
                request, scan=scan, findings=findings, page_count=len(raw_findings),
                targets=repository.list_scan_targets(scan_id), risk_filter=risk,
                review_filter=review, q_filter=q, sort_filter=sort,
                active_statuses=ACTIVE_STATUSES,
            ),
        )

    @app.get("/scans/{scan_id}/findings", response_class=HTMLResponse)
    def scan_findings(request: Request, scan_id: int, risk: str = "", review: str = "",
                      q: str = "", sort: str = "priority"):
        if not repository.get_scan(scan_id):
            raise HTTPException(404)
        findings = repository.list_finding_groups(
            scan_id, risk=risk, review=review, q=q, sort=sort,
        )
        return templates.TemplateResponse(
            request, "partials/findings_table.html",
            context(request, findings=findings),
        )

    @app.get("/scans/{scan_id}/status", response_class=HTMLResponse)
    def scan_status(request: Request, scan_id: int):
        scan = repository.get_scan(scan_id)
        if not scan:
            raise HTTPException(404)
        if scan["status"] not in ACTIVE_STATUSES and request.headers.get("HX-Request") == "true":
            return Response(headers={"HX-Refresh": "true"})
        return templates.TemplateResponse(
            request, "partials/scan_status.html",
            context(request, scan=scan, active_statuses=ACTIVE_STATUSES),
        )

    @app.post("/scans/{scan_id}/cancel")
    def cancel_scan(scan_id: int, csrf_token_value: str = Form(..., alias="csrf_token")):
        check_csrf(csrf_token_value)
        repository.request_cancel(scan_id)
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)

    @app.post("/scans/{scan_id}/resume")
    def resume_scan(scan_id: int, csrf_token_value: str = Form(..., alias="csrf_token")):
        check_csrf(csrf_token_value)
        if repository.resume_scan(scan_id):
            worker.wake()
        return RedirectResponse(f"/scans/{scan_id}", status_code=303)

    @app.post("/scans/{scan_id}/delete")
    def delete_scan(scan_id: int, csrf_token_value: str = Form(..., alias="csrf_token")):
        check_csrf(csrf_token_value)
        scan = repository.get_scan(scan_id)
        if not scan:
            raise HTTPException(404)
        if scan["status"] in ACTIVE_STATUSES:
            raise HTTPException(409, "An active scan cannot be deleted")
        repository.delete_scan(scan_id)
        shutil.rmtree(data_dir / "scans" / str(scan_id), ignore_errors=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/findings", response_class=HTMLResponse)
    def investigations(request: Request, risk: str = "", review: str = "",
                       q: str = "", sort: str = "company"):
        companies = repository.list_company_investigations(
            risk=risk, review=review, q=q, sort=sort,
        )
        return templates.TemplateResponse(
            request, "findings.html",
            context(
                request, companies=companies, risk_filter=risk,
                review_filter=review, q_filter=q, sort_filter=sort,
            ),
        )

    @app.get("/findings/list", response_class=HTMLResponse)
    def investigations_list(request: Request, risk: str = "", review: str = "",
                            q: str = "", sort: str = "company"):
        companies = repository.list_company_investigations(
            risk=risk, review=review, q=q, sort=sort,
        )
        return templates.TemplateResponse(
            request, "partials/company_investigations.html",
            context(request, companies=companies),
        )

    def company_for_key(company_key: str) -> dict:
        company = next(
            (
                item for item in repository.list_company_investigations(sort="company")
                if item["company_key"] == company_key
            ),
            None,
        )
        if not company:
            raise HTTPException(404, "Company investigation not found")
        return company

    @app.get("/outreach", response_class=HTMLResponse)
    def outreach(request: Request):
        return templates.TemplateResponse(
            request, "outreach.html",
            context(request, outreach_companies=repository.list_outreach_companies()),
        )

    @app.post("/outreach/add")
    def add_outreach(
        request: Request, company_key: str = Form(...),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        company = company_for_key(company_key)
        repository.add_company_outreach(company)
        if request.headers.get("HX-Request") == "true":
            return Response(headers={
                "HX-Refresh": "true",
                "HX-Trigger": json.dumps(
                    {"showToast": {"message": "Company added to the outreach list", "tone": "success"}},
                ),
            })
        return RedirectResponse("/findings", status_code=303)

    @app.post("/outreach/remove")
    def remove_outreach(
        request: Request, company_key: str = Form(...), return_to: str = Form("/outreach"),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        repository.remove_company_outreach(company_key)
        if request.headers.get("HX-Request") == "true":
            return Response(headers={
                "HX-Refresh": "true",
                "HX-Trigger": json.dumps(
                    {"showToast": {"message": "Company removed from the outreach list", "tone": "success"}},
                ),
            })
        safe_return = return_to if return_to in {"/outreach", "/findings"} else "/outreach"
        return RedirectResponse(safe_return, status_code=303)

    @app.get("/findings/{finding_id}", response_class=HTMLResponse)
    def finding_detail(request: Request, finding_id: int):
        finding = repository.get_finding(finding_id)
        if not finding:
            raise HTTPException(404)
        return templates.TemplateResponse(
            request, "finding_detail.html",
            context(request, finding=finding, related=repository.related_findings(finding_id)),
        )

    @app.post("/findings/{finding_id}/review")
    def update_review(
        request: Request, finding_id: int, review_status: str = Form(...), review_note: str = Form(""),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        if review_status not in REVIEW_STATUSES:
            raise HTTPException(400, "Invalid review status")
        repository.update_review(finding_id, review_status, review_note)
        if request.headers.get("HX-Request") == "true":
            finding = repository.get_finding(finding_id)
            response = templates.TemplateResponse(
                request, "partials/review_form.html", context(request, finding=finding),
            )
            response.headers["HX-Trigger"] = json.dumps(
                {"showToast": {"message": "Review saved", "tone": "success"}},
                ensure_ascii=False,
            )
            return response
        return RedirectResponse(f"/findings/{finding_id}", status_code=303)

    @app.get("/findings/{finding_id}/screenshot")
    def screenshot(finding_id: int):
        finding = repository.get_finding(finding_id)
        if not finding or not finding["screenshot_path"]:
            raise HTTPException(404)
        path = Path(finding["screenshot_path"]).resolve()
        if not path.is_relative_to(data_dir) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.get("/mappings", response_class=HTMLResponse)
    def mappings(request: Request, q: str = "", status: str = ""):
        return templates.TemplateResponse(
            request, "mappings.html",
            context(
                request, mappings=repository.list_mappings(q=q, status=status),
                mapping_stats=repository.mapping_stats(), q_filter=q, status_filter=status,
            ),
        )

    @app.get("/mappings/list", response_class=HTMLResponse)
    def mappings_list(request: Request, q: str = "", status: str = ""):
        return templates.TemplateResponse(
            request, "partials/mappings_list.html",
            context(request, mappings=repository.list_mappings(q=q, status=status)),
        )

    @app.post("/mappings/{brand_id}/confirm")
    def confirm_mapping(
        request: Request, brand_id: int, ticker: str = Form(...), company_name: str = Form(...),
        csrf_token_value: str = Form(..., alias="csrf_token"),
    ):
        check_csrf(csrf_token_value)
        FinanceService().confirm_candidate(repository, brand_id, ticker, company_name)
        if request.headers.get("HX-Request") == "true":
            return Response(
                headers={
                    "HX-Refresh": "true",
                    "HX-Trigger": json.dumps(
                        {"showToast": {"message": "Company mapping updated", "tone": "success"}},
                        ensure_ascii=False,
                    ),
                }
            )
        return RedirectResponse("/mappings", status_code=303)

    @app.get("/scans/{scan_id}/export/{file_type}")
    def export_scan(scan_id: int, file_type: str):
        scan = repository.get_scan(scan_id)
        if not scan:
            raise HTTPException(404)
        rows = repository.list_findings(scan_id)
        filename = f"fake-shop-scan-{scan_id}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}.{file_type}"'}
        if file_type == "json":
            safe_rows = [{key: value for key, value in row.items() if key != "screenshot_path"} for row in rows]
            payload = json.dumps(safe_rows, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            return Response(payload, media_type="application/json", headers=headers)
        if file_type == "xlsx":
            stream = _xlsx_export(rows)
            return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)
        if file_type == "html":
            return HTMLResponse(_html_export(scan, rows), headers=headers)
        raise HTTPException(404)

    return app


def _xlsx_export(rows: list[dict]) -> io.BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Results"
    columns = [
        ("brand", "Brand"), ("topic", "Topic"), ("url", "URL"),
        ("domain", "Domain"), ("risk_score", "Risk"), ("risk_level", "Risk level"),
        ("priority_score", "Priority"), ("parent_company", "Parent company"),
        ("ticker", "Ticker"), ("market_cap_usd", "Market cap USD"),
        ("review_status", "Review"), ("review_note", "Review note"), ("error", "Error"),
    ]
    sheet.append([label for _, label in columns])
    for row in rows:
        sheet.append([row.get(key, "") for key, _ in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _html_export(scan: dict, rows: list[dict]) -> str:
    cards = []
    for row in rows:
        evidence = "".join(
            f"<li>{html.escape(item['label'])}: {item['points']} — {html.escape(item['detail'])}</li>"
            for item in row["evidence"]
        ) or "<li>No risk signals were detected</li>"
        cards.append(
            f"<article><h2>{html.escape(row['brand'])} — {row['risk_score']}/100</h2>"
            f"<p dir='ltr'>{html.escape(row['url'])}</p><ul>{evidence}</ul></article>"
        )
    return f"""<!doctype html><html lang='en' dir='ltr'><meta charset='utf-8'>
    <title>Fake Shop Scan {scan['id']}</title><style>
    body{{font-family:Arial;max-width:1000px;margin:auto;padding:32px;background:#f5f7fa}}
    article{{background:white;padding:20px;margin:16px 0;border:1px solid #dfe3e8;border-radius:12px}}
    </style><h1>Scan #{scan['id']}</h1>{''.join(cards)}</html>"""


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
