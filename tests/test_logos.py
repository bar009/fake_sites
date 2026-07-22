from pathlib import Path

from fakeshop.logos import CompanyLogoCache


PNG = b"\x89PNG\r\n\x1a\n" + b"fake-logo"


class FakeResponse:
    status_code = 200
    content = PNG
    is_redirect = False
    headers = {}


def test_company_logo_is_normalized_downloaded_and_cached(monkeypatch, tmp_path: Path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr("fakeshop.logos.requests.get", fake_get)
    cache = CompanyLogoCache(tmp_path / "logos")
    assert cache.normalize_name("Aimé Leon Dore") == cache.normalize_name("Aime Leon Dore")

    first = cache.get("https://WWW.Example.com/about")
    second = cache.get("www.example.com")

    assert first == (PNG, "image/png")
    assert second == first
    assert len(calls) == 1
    assert calls[0][0].endswith("/example.com.ico")
    assert calls[0][1]["allow_redirects"] is False


def test_company_logo_rejects_non_domain_values_without_network(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "fakeshop.logos.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )
    cache = CompanyLogoCache(tmp_path / "logos")

    assert cache.get("localhost") is None
    assert cache.get("not a domain") is None


def test_company_logo_resolves_exact_business_name_through_wikidata(monkeypatch, tmp_path: Path):
    calls = []

    class JsonResponse:
        status_code = 200
        content = b""
        is_redirect = False
        headers = {}

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    responses = [
        JsonResponse({"search": [
            {"id": "Q1", "label": "Example", "description": "American clothing brand"},
        ]}),
        JsonResponse({"entities": {"Q1": {"claims": {"P856": [
            {"mainsnak": {"datavalue": {"value": "https://www.example.com/about"}}},
        ]}}}}),
        FakeResponse(),
    ]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    monkeypatch.setattr("fakeshop.logos.requests.get", fake_get)
    cache = CompanyLogoCache(tmp_path / "logos")

    assert cache.get("", "Example") == (PNG, "image/png")
    assert len(calls) == 3
    assert calls[0][1]["params"]["action"] == "wbsearchentities"
    assert calls[1][1]["params"]["action"] == "wbgetentities"
    assert calls[2][0].endswith("/example.com.ico")
