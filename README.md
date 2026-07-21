# Fake Shop Checker — Brand Impersonation Investigation Center

Fake Shop Checker is a local research application for finding `.shop` websites that may be impersonating brands. It searches for known storefront-template fingerprints, captures pages in an isolated browser, checks domain age, and produces an explainable risk score. Parent-company market capitalization contributes to a separate priority score and does not determine whether a site is suspicious.

> The system prioritizes findings for human review. It does not claim that a website is definitively fake.

## Installation

```powershell
cd C:\dev\fake-shop-checker
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

## Local web interface

```powershell
.venv\Scripts\python -m fakeshop.web
```

Open `http://127.0.0.1:8000` after startup. The server listens on localhost only.

The web interface can:

- Upload a CSV file of brands.
- Scan one brand or a specific URL.
- Track, stop, and resume persistent background jobs.
- Display screenshots, risk scores, evidence, and priority scores.
- Mark a finding as unreviewed, confirmed suspicious, false positive, or requiring investigation.
- Export JSON, Excel, or HTML reports.

Application data is stored under `data/` and is excluded from Git. Market-cap data comes from Yahoo Finance through the unofficial `yfinance` package. It is intended for personal research and may be missing or delayed. The source and update time are displayed on every screen.

## CSV format

The `brand` column is required. All other columns are optional:

```csv
brand,topic,parent_company,ticker,official_domain
Nike,sportswear,Nike Inc,NKE,nike.com
Lego,toys,,,
```

Explicit `parent_company` and `ticker` values take precedence over automatic mapping.

The repository also contains `brands_1000.csv`, an expanded catalog of 1,000 scan targets across 50 topics. It combines 300 consumer brands with a high impersonation risk and 700 large public companies selected by market capitalization from the [Nasdaq Stock Screener](https://www.nasdaq.com/market-activity/stocks/screener). Duplicate share classes and securities were removed. `official_domain` remains blank when no verified source was available, avoiding guessed domains.

## Existing CLI

The original command remains available:

```powershell
.venv\Scripts\python check_brands.py brands.csv
```

Useful options:

- `--top 5` — number of results per brand.
- `--brand Nike` — scan one brand from the file.
- `--topic watches` — scan one topic.
- `--resume` — resume the latest interrupted run.
- `--provider brave` — use the Brave Search API with `BRAVE_API_KEY` in `.env`.

CLI outputs are stored under `runs/` and excluded from Git.

## Safety

Scanned websites may be hostile. The application:

- Displays suspect URLs as copyable text rather than clickable links in the investigation UI.
- Blocks localhost, private and link-local addresses, and ports other than 80/443.
- Creates a temporary browser context for each site and blocks downloads and service workers.
- Does not click, submit forms, or download files.

## Tests

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Next steps

- Research additional search fingerprints beyond `What Our Customers Say`.
- Carefully expand to `.store`, `.online`, `.site`, and other TLDs.
- Add Certificate Transparency, similar-domain, passive DNS, and abuse-data discovery.
- Add scheduled monitoring, run comparison, and alerts.
- Move to a licensed financial-data source if the application becomes commercial.
