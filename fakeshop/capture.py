"""Visit suspect URLs with headless Chromium and take full-page screenshots.

These are hostile sites: only load the page and screenshot it - never click,
fill forms, or download anything. A failure on one URL is recorded and the
batch continues.
"""

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

NAV_TIMEOUT_MS = 20_000
SETTLE_MS = 2_500  # let lazy images/JS render before the shot

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@dataclass
class CaptureResult:
    final_url: str = ""
    http_status: int | None = None
    page_title: str = ""
    screenshot: str = ""   # filename relative to the run folder, "" on failure
    error: str = ""


class Capturer:
    """One shared browser for the whole run; a fresh page per URL."""

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            locale="en-US",
        )
        self._context.set_default_timeout(NAV_TIMEOUT_MS)
        return self

    def __exit__(self, *exc):
        self._context.close()
        self._browser.close()
        self._pw.stop()
        return False

    def capture(self, url: str, screenshot_path: Path) -> CaptureResult:
        result = CaptureResult()
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(SETTLE_MS)
            result.final_url = page.url
            result.http_status = response.status if response else None
            result.page_title = page.title()
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            result.screenshot = screenshot_path.name
        except Exception as e:  # noqa: BLE001 - keep the batch alive
            result.error = f"{type(e).__name__}: {e}"
        finally:
            page.close()
        return result
