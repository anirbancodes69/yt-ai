from __future__ import annotations

from datetime import date

from channel.data import Dataset
from channel.facts import build_facts, product_id_of, subset_for
from channel.scoring import ScoringConfig, score_product

AS_OF = date(2026, 11, 20)
BANDS = (10000, 15000, 20000, 30000, 50000)


def _facts(dataset, cfg, as_of=AS_OF):
    return build_facts(
        dataset,
        cfg,
        as_of=as_of,
        bands=BANDS,
        max_price_age_days=7,
        history_days=90,
        lookback_days=30,
    )


def test_spec_facts_carry_source(dataset: Dataset, cfg: ScoringConfig) -> None:
    f = _facts(dataset, cfg)
    fact = f["products.tarang-t9.specs.battery_mah"]
    assert fact.value == 5000
    assert fact.source == dataset.product("tarang-t9").specs["battery_mah"].source_url


def test_price_facts(dataset: Dataset, cfg: ScoringConfig) -> None:
    f = _facts(dataset, cfg)
    assert f["prices.ostra-o2.current"].value == 19999
    assert f["prices.ostra-o2.highest_90d"].value == 22999
    assert f["prices.ostra-o2.lowest_90d"].value == 19999
    assert f["prices.ostra-o2.drop_from_high_90d"].value == 3000


def test_scores_are_rounded_to_one_decimal(dataset: Dataset, cfg: ScoringConfig) -> None:
    f = _facts(dataset, cfg)
    exact = score_product(dataset.product("tarang-t9"), cfg).overall
    assert f["scores.tarang-t9.overall"].value == round(exact, 1)


def test_ranking_facts(dataset: Dataset, cfg: ScoringConfig) -> None:
    f = _facts(dataset, cfg)
    assert f["ranking.20000.tarang-t9.rank"].value == 1
    assert f["ranking.20000.count"].value == 6
    assert f["ranking.20000.band"].value == 20000
    assert "ranking.20000.zentra-z8-pro.rank" not in f  # above the band


def test_previous_ranking_uses_lookback(dataset: Dataset, cfg: ScoringConfig) -> None:
    f = _facts(dataset, cfg)
    # 30 days earlier the Ostra O2 still cost 22,999, so it was outside the 20k band.
    assert "ranking_prev.20000.ostra-o2.rank" not in f
    assert f["ranking_prev.30000.ostra-o2.rank"].value >= 1


def test_product_id_of() -> None:
    assert product_id_of("products.a-b.specs.ram_gb") == "a-b"
    assert product_id_of("prices.a-b.current") == "a-b"
    assert product_id_of("ranking.20000.a-b.rank") == "a-b"
    assert product_id_of("ranking.20000.count") is None
    assert product_id_of("meta.as_of") is None


def test_subset_only_contains_brief_products_and_band(dataset, cfg) -> None:
    sub = subset_for(_facts(dataset, cfg), ("tarang-t9",), 20000)
    pids = {product_id_of(k) for k in sub} - {None}
    assert pids == {"tarang-t9"}
    assert "ranking.20000.count" in sub
    assert not any(k.startswith("ranking.30000") for k in sub)
    assert list(sub) == sorted(sub)
