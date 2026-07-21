from fakeshop.scoring import assess_risk, impact_score, priority_score


def test_explainable_high_risk_score():
    result = assess_risk(
        brand="Nike",
        url="https://nike-outlet.shop/deal",
        final_url="https://checkout-other.shop/deal",
        page_text="What Are The Costumers Say — Costumer Reviews",
        domain_age_days=24,
    )
    assert result["score"] == 100
    assert result["level"] == "high"
    assert {item["code"] for item in result["evidence"]} == {
        "template_fingerprint", "secondary_template_marker", "young_domain",
        "brand_domain_pattern", "cross_domain_redirect",
    }


def test_missing_information_does_not_add_risk():
    result = assess_risk(brand="Nike", url="https://nike.shop")
    assert result == {"score": 0, "level": "low", "evidence": []}


def test_business_impact_and_priority_are_separate():
    assert impact_score(1_000_000_000) == 25
    assert impact_score(5_000_000_000) == 50
    assert impact_score(50_000_000_000) == 75
    assert impact_score(500_000_000_000) == 100
    assert priority_score(60, None) == 60
    assert priority_score(60, 500_000_000_000) == 70
