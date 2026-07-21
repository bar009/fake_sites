from pathlib import Path

from fakeshop.report import write_html


def test_cli_html_report_is_english(tmp_path: Path):
    output = tmp_path / "report.html"
    write_html(
        [{"brand": "Example", "topic": "retail", "url": "", "flags": []}],
        output,
        "test-run",
    )
    report = output.read_text(encoding="utf-8")
    assert '<html lang="en" dir="ltr">' in report
    assert "Suspected Fake Shop Report" in report
    assert "No search results were found" in report
    assert not any("\u0590" <= char <= "\u05ff" for char in report)
