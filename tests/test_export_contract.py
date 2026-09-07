from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_export_module_contains_real_timestamp_formatters():
    source = (ROOT / "static/export.js").read_text(encoding="utf-8")
    for name in ["formatVtt", "formatSrt", "formatMarkdown", "formatPlainText"]:
        assert f"function {name}" in source
    assert "toISOString" not in source


def test_review_page_has_search_notes_and_star_controls():
    html = (ROOT / "templates/review.html").read_text(encoding="utf-8")
    for marker in ["sessionList", "searchInput", "noteInput", "starred"]:
        assert marker in html
