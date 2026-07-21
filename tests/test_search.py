from ddgs.exceptions import DDGSException

from fakeshop.search import DdgsProvider, build_query


def test_query_uses_new_customer_phrase():
    assert build_query("Nike") == 'site:.shop "What Our Customer Say" "Nike"'


def test_duckcamp_query_includes_spaced_brand_alias():
    query = build_query("DUCKCAMP")
    assert query == 'site:.shop "What Our Customer Say" "Duck Camp"'


def test_ddgs_falls_back_to_yahoo_when_duckduckgo_has_no_results(monkeypatch):
    calls = []

    def fake_text(_self, _query, **kwargs):
        calls.append(kwargs["backend"])
        if kwargs["backend"] == "duckduckgo":
            raise DDGSException("No results found.")
        return [{
            "href": "https://duckcampstore.shop/",
            "title": "Premium Outdoor Gear | Duck Camp",
            "body": "What Our Customer Say",
        }]

    monkeypatch.setattr("ddgs.ddgs.DDGS.text", fake_text)
    results = DdgsProvider(delay_range=(0, 0)).search(build_query("DUCKCAMP"), top=3)

    assert calls == ["duckduckgo", "yahoo"]
    assert [result.url for result in results] == ["https://duckcampstore.shop/"]
