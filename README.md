# Fake Shop Checker — Brand Impersonation Investigation Center

Fake Shop Checker is a local research application for finding `.shop` websites that may be impersonating brands. It searches for known storefront-template fingerprints, captures pages in an isolated browser, checks domain age, and produces an explainable risk score. Parent-company market capitalization contributes to a separate priority score and does not determine whether a site is suspicious.

> The system prioritizes findings for human review. It does not claim that a website is definitively fake.

## Windows setup — start here

This section assumes you have a Windows 10 or Windows 11 computer and have not installed any developer tools. You do not need Git, PowerShell knowledge, or any paid software.

You will need:

- An internet connection for the first installation.
- About 2 GB of free disk space.
- Permission to install Python on the computer.

### Step 1 — install Python

1. Open the official [Python for Windows download page](https://www.python.org/downloads/windows/).
2. Select the yellow **Download Python 3** button near the top of the page. Python 3.11 or newer is suitable.
3. When the download finishes, open your **Downloads** folder and double-click the file whose name begins with `python-` and ends with `.exe`.
4. On the first installer screen, select the checkbox labeled **Add python.exe to PATH**. This checkbox is important.
5. Select **Install Now**.
6. If Windows asks whether the installer may make changes to the computer, select **Yes**.
7. Wait for the message **Setup was successful**, then select **Close**.

You do not need to open Python after installing it.

### Step 2 — download Fake Shop Checker

1. Select this link: [Download Fake Shop Checker for Windows](https://github.com/bar009/fake_sites/archive/refs/heads/main.zip).
2. Save the ZIP file when your browser asks what to do.
3. Open your **Downloads** folder.
4. Right-click `fake_sites-main.zip` and select **Extract All...**.
5. Select **Extract** in the window that appears.
6. Open the extracted `fake_sites-main` folder. Continue opening folders until you can see `README.md`, `requirements.txt`, and `start_windows.bat` together.

Do not run the application while it is still inside the ZIP file. It must be extracted first.

### Step 3 — start the application

1. Double-click `start_windows.bat`. Windows may display it as **start_windows** with the type **Windows Batch File**.
2. A black setup window will open. Keep this window open.
3. The first start creates a private Python environment and downloads the application packages and a private Chromium browser. This normally takes 5–15 minutes, depending on the internet connection.
4. When setup finishes, the application opens automatically in your normal web browser.
5. If the browser does not open, manually open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The black window must remain open while you use Fake Shop Checker. It is the local application server.

### Step 4 — stop the application

1. Return to the black Fake Shop Checker window.
2. Press `Ctrl+C` on the keyboard.
3. If Windows asks `Terminate batch job (Y/N)?`, type `Y` and press Enter.
4. You can now close the black window and the browser tab.

### Open it again another day

Open the extracted `fake_sites-main` folder and double-click `start_windows.bat` again. The first-time installation does not need to be repeated. The browser should open within a few seconds.

### Where your information is saved

Scans, screenshots, the local database, and exported reports are saved in the `data` folder inside `fake_sites-main`. They stay on your computer and are not uploaded to GitHub.

To back up or move your investigation history, close Fake Shop Checker and copy the entire `data` folder to a safe location.

### Windows troubleshooting

- **The window says Python is not installed:** repeat Step 1. Make sure **Add python.exe to PATH** is selected, then restart the computer and double-click `start_windows.bat` again.
- **The first setup appears to be stuck:** package and Chromium downloads can take several minutes. Keep the black window open and check that the computer is connected to the internet.
- **The browser says the page cannot be reached:** check that the black window is still open. Wait 10 seconds and refresh the page.
- **The message says port 8000 is already in use:** Fake Shop Checker is probably already open in another black window. Try opening [http://127.0.0.1:8000](http://127.0.0.1:8000), or close the older window first.
- **Windows Firewall asks for access:** Fake Shop Checker only uses this computer. You do not need to enable access on public networks.
- **The computer went to sleep during a scan:** scanning pauses while Windows is asleep. After the application starts again, unfinished scans automatically return to the queue.
- **Installation fails on a Windows ARM computer:** `start_windows.bat` automatically tries the ARM-compatible installation method after the normal method fails.

The application listens only on `127.0.0.1`, which means another computer cannot open it over the network.

### Manual start for experienced users

The one-click file performs the standard setup automatically. Developers who prefer a terminal can use:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m fakeshop.web
```

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
