"""Fake brand-site checker.

For every brand in the input CSV: search 'site:.shop "What Are The Costumers Say"
"<brand>"', open the top N hits in headless Chromium, screenshot each, look up the
domain registration via RDAP, and write results.xlsx + report.html + results.json
into a timestamped folder under runs/.

Usage:
    python check_brands.py brands.csv [--top 3] [--provider ddgs|brave] [--brand X]
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from fakeshop.capture import Capturer
from fakeshop.report import write_html, write_json, write_xlsx
from fakeshop.search import build_query, get_provider
from fakeshop.whois_check import WhoisChecker, domain_of


def load_brands(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "brand" not in [c.strip().lower() for c in reader.fieldnames]:
            sys.exit(f"ERROR: {csv_path} needs a header row with a 'brand' column "
                     "(optional 'topic' column).")
        brands = []
        for raw in reader:
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if row.get("brand"):
                brands.append({"brand": row["brand"], "topic": row.get("topic", "")})
        return brands


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text).strip("_")[:60] or "unknown"


STATIC_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico",
               ".pdf", ".zip", ".css", ".js", ".mp4", ".woff", ".woff2")


def capture_target(url: str) -> tuple[str, str]:
    """Search engines sometimes return a site's raw image/asset files for these
    queries. The thing being investigated is the site, so capture its homepage
    instead of the file. Returns (url_to_capture, note)."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(STATIC_EXTS) or "/wp-content/uploads/" in path:
        return f"{parsed.scheme}://{parsed.netloc}/", "search hit was a file; captured site homepage"
    return url, ""


def main() -> None:
    # when output is redirected (background runs), Windows defaults to cp1252,
    # which crashes on non-Latin characters that appear in scam URLs/titles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Hunt fake .shop brand sites.")
    parser.add_argument("brands_csv", type=Path, help="CSV with columns: brand[,topic]")
    parser.add_argument("--top", type=int, default=3, help="results per brand (default 3)")
    parser.add_argument("--provider", choices=["ddgs", "brave"], default="ddgs",
                        help="search provider (default ddgs = DuckDuckGo, no key)")
    parser.add_argument("--brand", help="run only this brand from the CSV")
    parser.add_argument("--topic", help="run only brands with this topic")
    parser.add_argument("--resume", nargs="?", const="latest", default=None, metavar="RUN_DIR",
                        help="continue an interrupted run, skipping brands already done "
                             "(default: the most recent folder under runs/)")
    parser.add_argument("--list-topics", action="store_true",
                        help="print topics and brand counts, then exit")
    args = parser.parse_args()

    load_dotenv()
    brands = load_brands(args.brands_csv)

    if args.list_topics:
        counts: dict[str, int] = {}
        for b in brands:
            counts[b["topic"] or "(none)"] = counts.get(b["topic"] or "(none)", 0) + 1
        for topic, count in counts.items():
            print(f"{count:4}  {topic}")
        print(f"{len(brands):4}  TOTAL")
        return

    if args.topic:
        brands = [b for b in brands if b["topic"].lower() == args.topic.lower()]
        if not brands:
            sys.exit(f"ERROR: topic '{args.topic}' not found in {args.brands_csv}")
    if args.brand:
        brands = [b for b in brands if b["brand"].lower() == args.brand.lower()]
        if not brands:
            sys.exit(f"ERROR: brand '{args.brand}' not found in {args.brands_csv}")

    provider = get_provider(args.provider)
    whois = WhoisChecker()

    rows: list[dict] = []
    if args.resume:
        if args.resume == "latest":
            saved = sorted((Path(__file__).parent / "runs").glob("*/results.json"))
            if not saved:
                sys.exit("ERROR: --resume found no previous run under runs/")
            run_dir = saved[-1].parent
        else:
            run_dir = Path(args.resume)
            if not (run_dir / "results.json").is_file():
                sys.exit(f"ERROR: no results.json in {run_dir}")
        rows = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
        done = {r["brand"] for r in rows}
        brands = [b for b in brands if b["brand"] not in done]
        print(f"Resuming {run_dir.name}: {len(done)} brands already done, {len(brands)} to go")
    else:
        run_dir = Path(__file__).parent / "runs" / datetime.now().strftime("%Y-%m-%d_%H%M")
    shots_dir = run_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run folder: {run_dir}")
    print(f"{len(brands)} brand(s), top {args.top} results each, provider: {provider.name}\n")

    def flush() -> None:
        write_json(rows, run_dir / "results.json")
        write_xlsx(rows, run_dir / "results.xlsx")
        write_html(rows, run_dir / "report.html", run_dir.name)

    with Capturer() as capturer:
        for i, entry in enumerate(brands, start=1):
            brand, topic = entry["brand"], entry["topic"]
            query = build_query(brand)
            print(f"[{i}/{len(brands)}] {brand} ... ", end="", flush=True)

            try:
                results = provider.search(query, top=args.top)
            except Exception as e:  # noqa: BLE001 - record and move on
                print(f"SEARCH FAILED: {e}")
                rows.append({"brand": brand, "topic": topic, "rank": "", "url": "",
                             "flags": [], "error": f"search failed: {e}"})
                flush()
                continue

            # search engines sneak ad/redirect links (bing.com/aclick, ...) into
            # results despite the site:.shop operator - keep only .shop domains
            results = [r for r in results if domain_of(r.url).endswith(".shop")]

            if not results:
                print("no results")
                rows.append({"brand": brand, "topic": topic, "rank": "", "url": "",
                             "flags": [], "error": "no search results"})
                flush()
                continue

            print(f"{len(results)} result(s)")
            for rank, hit in enumerate(results, start=1):
                domain = domain_of(hit.url)
                print(f"    #{rank} {hit.url}")

                info = whois.lookup(hit.url)
                target_url, note = capture_target(hit.url)
                shot_path = shots_dir / f"{safe_name(brand)}_{rank}_{safe_name(domain)}.png"
                cap = capturer.capture(target_url, shot_path)

                root_url = f"https://{domain}/"
                if cap.error and target_url != root_url:
                    cap = capturer.capture(root_url, shot_path)
                    note = (note + "; " if note else "") + "original URL failed; captured site homepage"

                errors = "; ".join(e for e in (info.error, cap.error) if e)
                if cap.error:
                    print(f"       capture error: {cap.error}")

                rows.append({
                    "brand": brand,
                    "topic": topic,
                    "rank": rank,
                    "query": query,
                    "url": hit.url,
                    "final_url": cap.final_url,
                    "page_title": cap.page_title or hit.title,
                    "domain": domain,
                    "domain_created": info.created,
                    "domain_age_days": info.age_days,
                    "registrar": info.registrar,
                    "country": info.country,
                    "flags": list(info.flags),
                    "note": note,
                    "screenshot": cap.screenshot,
                    "error": errors,
                })

            # hours-long batches must survive a crash mid-run
            flush()

    flush()

    suspicious = sum(1 for r in rows if r.get("flags"))
    found = sum(1 for r in rows if r.get("url"))
    print(f"\nDone. {found} sites captured, {suspicious} flagged suspicious.")
    print(f"  Excel : {run_dir / 'results.xlsx'}")
    print(f"  Report: {run_dir / 'report.html'}")


if __name__ == "__main__":
    main()
