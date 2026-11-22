from __future__ import annotations

import math
import random
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from channel.data import Dataset, PricePoint
from channel.scoring import (
    ScoringConfig,
    ScoringError,
    compare,
    load_scoring_config,
    rank_band,
    score_product,
)
from tests.conftest import make_product

AS_OF = date(2026, 11, 20)


def _rank(dataset: Dataset, cfg: ScoringConfig, band: int, as_of: date = AS_OF):
    return rank_band(dataset, cfg, band_inr=band, as_of=as_of, max_price_age_days=7)


# ------------------------------------------------------------------ config validation


def _write_cfg(tmp_path: Path, cfg_path: Path, mutate) -> Path:
    raw = yaml.safe_load(cfg_path.read_text())
    mutate(raw)
    out = tmp_path / "scoring.yaml"
    out.write_text(yaml.safe_dump(raw, allow_unicode=True))
    return out


def test_config_loads(cfg: ScoringConfig) -> None:
    assert cfg.version == 1
    assert len(cfg.fingerprint) == 12
    assert math.isclose(sum(c.weight for c in cfg.categories.values()), 1)


@pytest.mark.parametrize(
    ("mutate", "msg"),
    [
        (lambda r: r["categories"]["camera"].__setitem__("weight", 0.5), "category weights"),
        (
            lambda r: r["categories"]["battery"]["metrics"][0].__setitem__("weight", 0.9),
            "metric weights",
        ),
        (
            lambda r: r["categories"]["battery"]["metrics"][0].__setitem__("max", 1000),
            "min < max",
        ),
        (
            lambda r: r["categories"]["battery"]["metrics"][0].__setitem__("spec", "nope"),
            "unknown spec",
        ),
        (
            lambda r: r["categories"]["camera"]["metrics"][0].__setitem__("spec", "ram_gb"),
            "bool metric needs a bool spec",
        ),
        (
            lambda r: r["categories"]["display"]["metrics"][0]["map"].__setitem__("AMOLED", 1),
            "lower-case",
        ),
        (lambda r: r["ranking"].__setitem__("weakness_threshold", 9), "weakness_threshold"),
    ],
)
def test_bad_config_rejected(tmp_path, settings, mutate, msg) -> None:
    path = _write_cfg(tmp_path, settings.paths.scoring, mutate)
    with pytest.raises(ValidationError, match=msg):
        load_scoring_config(path)


def test_fingerprint_changes_with_config(tmp_path, settings, cfg) -> None:
    path = _write_cfg(tmp_path, settings.paths.scoring, lambda r: r.__setitem__("version", 2))
    assert load_scoring_config(path).fingerprint != cfg.fingerprint


# ------------------------------------------------------------------ the formula itself


def test_hand_computed_score(dataset: Dataset, cfg: ScoringConfig) -> None:
    """Zentra Z5, worked by hand from config/scoring.yaml (documented in docs/SCORING.md)."""
    s = score_product(dataset.product("zentra-z5"), cfg)
    # performance: antutu (420000-300000)/1.2M = 0.1 *0.8 ; ram (4-4)/8 = 0 *0.2
    assert s.category("performance").score == pytest.approx(0.8)
    # display: lcd 0.4*0.4 ; 90Hz (30/84)*0.3 ; 600nits (100/1500)*0.3
    assert s.category("display").score == pytest.approx(
        10 * (0.4 * 0.4 + 30 / 84 * 0.3 + 100 / 1500 * 0.3)
    )
    # build: ip54 0.5*0.6 ; 192g inverted: 1 - (32/60) = 0.4667 *0.4
    assert s.category("build").score == pytest.approx(10 * (0.5 * 0.6 + (1 - 32 / 60) * 0.4))


def test_contributions_sum_to_overall(dataset: Dataset, cfg: ScoringConfig) -> None:
    for p in dataset.products.values():
        s = score_product(p, cfg)
        assert math.isclose(sum(c.contribution for c in s.categories), s.overall)
        for c in s.categories:
            assert math.isclose(sum(m.normalized * m.weight for m in c.metrics) * 10, c.score)
            assert 0 <= c.score <= 10


def test_linear_clamps(dataset: Dataset, cfg: ScoringConfig) -> None:
    huge = make_product(dataset, "huge", battery_mah=20000, charging_w=500)
    tiny = make_product(dataset, "tiny", battery_mah=1000, charging_w=1)
    assert score_product(huge, cfg).category("battery").score == pytest.approx(10)
    assert score_product(tiny, cfg).category("battery").score == pytest.approx(0)


def test_lower_is_better_inverts(dataset: Dataset, cfg: ScoringConfig) -> None:
    light = score_product(make_product(dataset, "light", weight_g=160), cfg)
    heavy = score_product(make_product(dataset, "heavy", weight_g=220), cfg)
    assert light.category("build").score > heavy.category("build").score


def test_unknown_map_value_fails_loudly(dataset: Dataset, cfg: ScoringConfig) -> None:
    with pytest.raises(ScoringError, match="not in scoring map"):
        score_product(make_product(dataset, "odd", display_type="e-ink"), cfg)


def test_map_lookup_is_case_insensitive(dataset: Dataset, cfg: ScoringConfig) -> None:
    upper = score_product(make_product(dataset, "u", ip_rating="IP64"), cfg)
    assert upper.overall == pytest.approx(score_product(dataset.product("tarang-t9"), cfg).overall)


def test_missing_spec_fails_loudly(dataset: Dataset, cfg: ScoringConfig) -> None:
    from channel.data import Product

    d = dataset.product("tarang-t9").model_dump(mode="json")
    del d["specs"]["battery_mah"]
    with pytest.raises(ScoringError, match="battery_mah"):
        score_product(Product.model_validate(d), cfg)


def test_metric_data_refs_resolve_to_data(dataset: Dataset, cfg: ScoringConfig) -> None:
    """Groundwork for the Phase 2 fact-checker: every data_ref points at the real value."""
    for p in dataset.products.values():
        for c in score_product(p, cfg).categories:
            for m in c.metrics:
                _, pid, _, key = m.data_ref.split(".")
                assert dataset.product(pid).specs[key].value == m.raw_value
                assert dataset.product(pid).specs[key].source_url == m.source_url


def test_strengths_and_weaknesses(dataset: Dataset, cfg: ScoringConfig) -> None:
    for p in dataset.products.values():
        s = score_product(p, cfg)
        assert len(s.strengths) <= 2 and len(s.weaknesses) <= 2
        assert all(s.category(n).score >= 7.5 for n in s.strengths)
        assert all(s.category(n).score <= 5.0 for n in s.weaknesses)
        scores = [s.category(n).score for n in s.strengths]
        assert scores == sorted(scores, reverse=True)


# ------------------------------------------------------------------ determinism


def test_scoring_is_deterministic(dataset: Dataset, cfg: ScoringConfig) -> None:
    a = [score_product(p, cfg).model_dump() for p in dataset.products.values()]
    b = [score_product(p, cfg).model_dump() for p in dataset.products.values()]
    assert a == b


def test_ranking_independent_of_input_order(dataset: Dataset, cfg: ScoringConfig) -> None:
    items = list(dataset.products.items())
    prices = list(dataset.prices)
    random.Random(7).shuffle(items)
    random.Random(7).shuffle(prices)
    shuffled = Dataset(products=dict(items), prices=tuple(prices))
    assert _rank(shuffled, cfg, 50000).model_dump() == _rank(dataset, cfg, 50000).model_dump()


def test_adding_a_product_does_not_change_other_scores(dataset: Dataset, cfg) -> None:
    before = score_product(dataset.product("ostra-o2"), cfg).overall
    flagship = make_product(dataset, "monster", antutu_v10=3_000_000)
    bigger = Dataset(products={**dataset.products, "monster": flagship}, prices=dataset.prices)
    assert score_product(bigger.product("ostra-o2"), cfg).overall == before


# ------------------------------------------------------------------ ranking


def test_band_20k_order_snapshot(dataset: Dataset, cfg: ScoringConfig) -> None:
    r = _rank(dataset, cfg, 20000)
    assert [e.product_id for e in r.entries] == [
        "tarang-t9",
        "ostra-o2",
        "pravah-p7",
        "lumora-l3",
        "kovix-k10",
        "zentra-z5",
    ]
    assert r.sample and r.as_of == AS_OF
    assert {x.product_id for x in r.excluded} == {"lumora-l9-ultra", "zentra-z8-pro"}


def test_bands_are_inclusive(dataset: Dataset, cfg: ScoringConfig) -> None:
    ids = [e.product_id for e in _rank(dataset, cfg, 18999).entries]
    assert "tarang-t9" in ids  # priced exactly 18,999
    assert "tarang-t9" not in [e.product_id for e in _rank(dataset, cfg, 18998).entries]


def test_stale_prices_are_excluded_with_reason(dataset: Dataset, cfg: ScoringConfig) -> None:
    r = _rank(dataset, cfg, 50000, as_of=date(2027, 1, 31))
    assert r.entries == ()
    assert all("no price within 7 days" in x.reason for x in r.excluded)


def test_equal_scores_break_on_price(dataset: Dataset, cfg: ScoringConfig) -> None:
    twin = make_product(dataset, "tarang-twin")  # identical specs to tarang-t9
    base = max(
        (p for p in dataset.prices if p.product_id == "tarang-t9"), key=lambda p: p.observed_on
    )
    cheaper = PricePoint.model_validate(
        base.model_dump(mode="json") | {"product_id": "tarang-twin", "price_inr": 15000}
    )
    ds = Dataset(
        products={**dataset.products, "tarang-twin": twin}, prices=(*dataset.prices, cheaper)
    )
    ids = [e.product_id for e in _rank(ds, cfg, 20000).entries]
    assert ids.index("tarang-twin") < ids.index("tarang-t9")


def test_ranking_cites_price_and_date(dataset: Dataset, cfg: ScoringConfig) -> None:
    for e in _rank(dataset, cfg, 50000).entries:
        assert e.price.observed_on <= AS_OF
        assert e.price.source_url.startswith("https://example.com/sample/")
        assert e.score_per_10k_inr == pytest.approx(e.overall / (e.price.price_inr / 10000))


# ------------------------------------------------------------------ head to head


def test_compare(dataset: Dataset, cfg: ScoringConfig) -> None:
    c = compare(dataset.product("tarang-t9"), dataset.product("ostra-o2"), cfg)
    assert c.overall_winner == "tarang-t9"
    by_name = {v.name: v for v in c.categories}
    assert by_name["performance"].winner == "ostra-o2"
    assert by_name["battery"].winner is None  # 6.26 vs 6.50 is inside the 0.3 tie margin


def test_compare_identical_is_tie(dataset: Dataset, cfg: ScoringConfig) -> None:
    c = compare(dataset.product("tarang-t9"), make_product(dataset, "twin"), cfg)
    assert c.overall_winner is None
    assert all(v.winner is None for v in c.categories)


def test_compare_self_rejected(dataset: Dataset, cfg: ScoringConfig) -> None:
    with pytest.raises(ScoringError):
        compare(dataset.product("tarang-t9"), dataset.product("tarang-t9"), cfg)


def test_hindi_and_english_labels(dataset: Dataset, cfg: ScoringConfig) -> None:
    hi = score_product(dataset.product("tarang-t9"), cfg, lang="hi")
    en = score_product(dataset.product("tarang-t9"), cfg, lang="en")
    assert hi.category("battery").label == "बैटरी"
    assert en.category("battery").label == "Battery"
    assert hi.overall == en.overall
