from fakeshop.search import DdgsProvider, build_query
from fakeshop.engine import ScanEngine


def test_query_uses_observed_customers_phrase():
    assert build_query("Nike") == 'site:.shop "What Our Customers Say" "Nike"'


def test_duckcamp_query_includes_spaced_brand_alias():
    query = build_query("DUCKCAMP")
    assert query == 'site:.shop "What Our Customers Say" "Duck Camp"'


def test_ddgs_falls_back_when_first_backend_has_only_blank_hits(monkeypatch):
    calls = []

    def fake_text(_self, _query, **kwargs):
        calls.append(kwargs["backend"])
        if kwargs["backend"] == "yahoo":
            return [{"href": "", "title": "", "body": ""}]
        return [{
            "href": "https://duckcampstore.shop/",
            "title": "Premium Outdoor Gear | Duck Camp",
            "body": "What Our Customer Say",
        }]

    monkeypatch.setattr("ddgs.ddgs.DDGS.text", fake_text)
    results = DdgsProvider(delay_range=(0, 0)).search(build_query("DUCKCAMP"), top=3)

    assert calls == ["yahoo", "duckduckgo"]
    assert [result.url for result in results] == ["https://duckcampstore.shop/"]


def test_brand_search_closes_playwright_before_using_provider(tmp_path):
    class FakeCapturer:
        closed = False

        def close(self):
            self.closed = True

    capturer = FakeCapturer()

    class FakeProvider:
        def search(self, _query, top):
            assert capturer.closed
            assert top == 3
            return []

    engine = ScanEngine.__new__(ScanEngine)
    engine.provider = FakeProvider()

    assert engine.scan_brand(
        "Duck Camp", top=3, screenshot_dir=tmp_path, capturer=capturer,
    ) == []
