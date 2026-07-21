from pathlib import Path

from fakeshop.web import load_csv_rows


def test_extended_brand_catalog_is_scanner_ready():
    path = Path(__file__).parents[1] / "brands_1000.csv"
    rows = load_csv_rows(path.read_bytes())
    names = {row["brand"].casefold() for row in rows}
    topics = {row["topic"] for row in rows}

    assert len(rows) == 1_000
    assert len(names) == 1_000
    assert len(topics) >= 40
    assert all(row["brand"] and row["topic"] for row in rows)
