"""Aggregate every run under runs/ into one master report at the project root.

For each brand, the most recent run that includes it wins. Outputs:
    master_report.html   - single page, all brands, flagged sites listed on top
    master_results.xlsx  - one table of everything

Screenshots stay in their run folders; the master page links into them,
so it must be opened from the project root (don't move the file alone).

Usage: python build_master_report.py
"""

import json
from pathlib import Path

from fakeshop.report import write_html, write_xlsx

ROOT = Path(__file__).parent


def main() -> None:
    runs_dir = ROOT / "runs"
    latest: dict[str, tuple[str, list[dict]]] = {}  # brand -> (run_name, its rows)

    for run_dir in sorted(runs_dir.iterdir()):  # names are timestamps => chronological
        results = run_dir / "results.json"
        if not results.is_file():
            continue
        by_brand: dict[str, list[dict]] = {}
        for row in json.loads(results.read_text(encoding="utf-8")):
            by_brand.setdefault(row["brand"], []).append(row)
        for brand, brand_rows in by_brand.items():
            latest[brand] = (run_dir.name, brand_rows)  # later runs overwrite earlier

    rows: list[dict] = []
    for brand in sorted(latest, key=lambda b: (latest[b][1][0].get("topic") or "", b.lower())):
        run_name, brand_rows = latest[brand]
        for row in brand_rows:
            row = dict(row)
            row["run"] = run_name
            if row.get("screenshot"):
                row["screenshot_href"] = f"runs/{run_name}/screenshots/{row['screenshot']}"
            rows.append(row)

    if not rows:
        print("No runs found under runs/ - nothing to aggregate.")
        return

    write_html(rows, ROOT / "master_report.html", "כל הריצות (master)")
    write_xlsx(rows, ROOT / "master_results.xlsx", extra_columns=(("run", "Run"),))

    brands = len(latest)
    flagged = sum(1 for r in rows if r.get("flags"))
    found = sum(1 for r in rows if r.get("url"))
    print(f"{brands} brands aggregated, {found} sites, {flagged} flagged suspicious.")
    print(f"  {ROOT / 'master_report.html'}")
    print(f"  {ROOT / 'master_results.xlsx'}")


if __name__ == "__main__":
    main()
