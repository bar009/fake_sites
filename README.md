# Fake Shop Checker — Brand Impersonation Investigation Center

Fake Shop Checker is a local research application for finding `.shop` websites that may be impersonating brands. It searches for known storefront-template fingerprints, captures pages in an isolated browser, checks domain age, and produces an explainable risk score. Parent-company market capitalization contributes to a separate priority score and does not determine whether a site is suspicious.

> The system prioritizes findings for human review. It does not claim that a website is definitively fake.

## Run on Windows

This guide is written for people who do not use Python or Git every day. The application runs only on your computer; it does not publish a website to the internet.

### 1. Install Python

1. Download Python 3.11 or newer from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
2. Start the installer.
3. On the first installer screen, select **Add python.exe to PATH**, then choose **Install Now**.
4. When installation finishes, open PowerShell and check that Python works:

```powershell
py --version
```

You should see `Python 3.11` or a newer version.

### 2. Download Fake Shop Checker (no Git required)

1. Download the [latest `main` branch ZIP](https://github.com/bar009/fake_sites/archive/refs/heads/main.zip).
2. If your browser asks for confirmation, choose **Keep** or **Save**. The download contains source code, not an installer.
3. Open the downloaded ZIP and select **Extract all**. Do not run the application from inside the ZIP.
4. Open the extracted `fake_sites-main` folder in File Explorer.
5. Right-click an empty area inside the folder and choose **Open in Terminal**.

Alternatively, users who already have Git can download the project with:

```powershell
git clone --branch main --single-branch https://github.com/bar009/fake_sites.git
cd fake_sites
```

### 3. Install the application

Paste these commands into the PowerShell window one at a time:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

The first installation may take several minutes. Playwright also downloads a private Chromium browser used to capture suspicious pages.

### 4. Start the application

Run:

```powershell
.\.venv\Scripts\python.exe -m fakeshop.web
```

Wait until the terminal shows that Uvicorn is running, then open [http://127.0.0.1:8000](http://127.0.0.1:8000) in Chrome, Edge, or Firefox. Keep the PowerShell window open while using the application.

To stop the application, click the PowerShell window and press `Ctrl+C`.

### Open it again later

There is no need to repeat the installation. Open the extracted project folder, choose **Open in Terminal**, and run only:

```powershell
.\.venv\Scripts\python.exe -m fakeshop.web
```

Your scans, screenshots, and reports remain in the local `data` folder. Back up that folder if you want to move the investigation history to another computer.

### Windows troubleshooting

- **`py` is not recognized:** reinstall Python and select **Add python.exe to PATH**, then close and reopen PowerShell.
- **`No module named ...`:** make sure the terminal is open in the extracted project folder, then repeat the four commands under **Install the application**.
- **Chromium is missing:** run `.\.venv\Scripts\python.exe -m playwright install chromium` again.
- **Port 8000 is already in use:** the application may already be running in another PowerShell window. Open [http://127.0.0.1:8000](http://127.0.0.1:8000), or stop the older process with `Ctrl+C`.
- **The computer went to sleep during a scan:** no local application can scan while Windows is asleep. When the application is running again, unfinished scans are automatically returned to the queue. A single timed-out website is retried once and cannot stop the rest of the batch.
- **Windows on an ARM64 computer:** if installing `ddgs` fails, follow the Windows ARM64 commands documented at the top of `requirements.txt`.

The server listens on `127.0.0.1` only, so other computers on the network cannot open it. Windows Firewall access is not required for normal local use.

## Local web interface

The web interface can:

- Upload a CSV file of brands.
- Scan one brand or a specific URL.
- Track, stop, and resume persistent background jobs.
- Display screenshots, risk scores, evidence, and priority scores.
- Mark a finding as unreviewed, confirmed suspicious, false positive, or requiring investigation.
- Export JSON, Excel, or HTML reports.

Application data is stored under `data/` and is excluded from Git. Market-cap data comes from Yahoo Finance through the unofficial `yfinance` package. It is intended for personal research and may be missing or delayed. The source and update time are displayed on every screen.

### Scan recovery and timeouts

Interrupted scans are returned to the queue automatically when the web process starts again. Each brand target runs in a disposable child process with a heartbeat and a hard safety deadline. A timed-out or crashed target is retried once in a fresh process; if it still fails, the target is marked failed and the rest of the batch continues.

The default target deadline is `120 + (top_n × 45)` seconds. Set `FAKESHOP_TARGET_TIMEOUT_SECONDS` in `.env` to use a fixed deadline (minimum 30 seconds). Worker diagnostics are stored under `data/worker/` and remain excluded from Git.

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
