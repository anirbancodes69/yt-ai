"""The fact table: every value a script may state, keyed by a stable reference.

Writers receive (a subset of) this table and must cite a key for every number
they say. The fact-checker rebuilds the table independently from the data
files, so it never trusts what the writer was given.

Keys
----
meta.as_of                         ISO date prices are quoted at
meta.score_scale                   10
meta.history_days                  price-history window (90)
meta.value_per_inr                 10000 (for "score per ₹10,000")
products.<id>.specs.<key>          spec value
products.<id>.launch.price_inr     launch price
products.<id>.launch.launched_on   ISO launch date
prices.<id>.current                current price (see data.pricing.current_price)
prices.<id>.lowest_90d / highest_90d / drop_from_high_90d
scores.<id>.overall                overall score, 1 decimal
scores.<id>.<category>             category score, 1 decimal
ranking.<band>.band                the band itself (e.g. 20000)
ranking.<band>.count               number of products in the band
ranking.<band>.<id>.rank           rank within the band
ranking.<band>.<id>.score_per_10k  overall / (price / 10,000), 1 decimal
ranking_prev.<band>.<id>.rank      rank ``lookback_days`` earlier (monthly refresh)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from channel.data.models import Dataset
from channel.data.pricing import current_price, price_history
from channel.scoring import ScoringConfig, rank_band, score_product

FactValue = bool | int | float | str
VALUE_PER_INR = 10_000


@dataclass(frozen=True)
class Fact:
    value: FactValue
    source: str  # a source_url, or "computed:<how>"


Facts = dict[str, Fact]


def _r1(x: float) -> float:
    return round(x, 1)


def product_id_of(ref: str) -> str | None:
    """The product a reference is about, or None for meta/band-level refs."""
    parts = ref.split(".")
    if parts[0] in ("products", "prices", "scores") and len(parts) >= 3:
        return parts[1]
    if parts[0] in ("ranking", "ranking_prev") and len(parts) == 4:
        return parts[2]
    return None


def build_facts(
    dataset: Dataset,
    cfg: ScoringConfig,
    *,
    as_of: date,
    bands: tuple[int, ...],
    max_price_age_days: int,
    history_days: int,
    lookback_days: int = 30,
) -> Facts:
    facts: Facts = {}
    scoring_src = f"computed:scoring v{cfg.version} ({cfg.fingerprint})"
    scale = int(cfg.scale) if float(cfg.scale).is_integer() else cfg.scale

    facts["meta.as_of"] = Fact(as_of.isoformat(), "computed:settings")
    facts["meta.score_scale"] = Fact(scale, scoring_src)
    facts["meta.history_days"] = Fact(history_days, "computed:settings")
    facts["meta.value_per_inr"] = Fact(VALUE_PER_INR, "computed:definition")

    window_start = as_of - timedelta(days=history_days)
    for pid, p in sorted(dataset.products.items()):
        for key, spec in p.specs.items():
            facts[f"products.{pid}.specs.{key}"] = Fact(spec.value, spec.source_url)
        facts[f"products.{pid}.launch.price_inr"] = Fact(p.launch.price_inr, p.launch.source_url)
        facts[f"products.{pid}.launch.launched_on"] = Fact(
            p.launch.launched_on.isoformat(), p.launch.source_url
        )

        cur = current_price(dataset.prices, pid, as_of=as_of, max_age_days=max_price_age_days)
        hist = [h for h in price_history(dataset.prices, pid, as_of=as_of) if h.on >= window_start]
        if cur is not None:
            facts[f"prices.{pid}.current"] = Fact(cur.price_inr, cur.source_url)
        if hist:
            lo = min(hist, key=lambda h: (h.price_inr, h.on))
            hi = max(hist, key=lambda h: (h.price_inr, h.on))
            facts[f"prices.{pid}.lowest_90d"] = Fact(lo.price_inr, lo.source.source_url)
            facts[f"prices.{pid}.highest_90d"] = Fact(hi.price_inr, hi.source.source_url)
            if cur is not None:
                facts[f"prices.{pid}.drop_from_high_90d"] = Fact(
                    hi.price_inr - cur.price_inr, "computed:highest_90d - current"
                )

        s = score_product(p, cfg)
        facts[f"scores.{pid}.overall"] = Fact(_r1(s.overall), scoring_src)
        for c in s.categories:
            facts[f"scores.{pid}.{c.name}"] = Fact(_r1(c.score), scoring_src)

    for prefix, when in (("ranking", as_of), ("ranking_prev", as_of - timedelta(lookback_days))):
        for band in bands:
            r = rank_band(
                dataset, cfg, band_inr=band, as_of=when, max_price_age_days=max_price_age_days
            )
            if prefix == "ranking":
                facts[f"ranking.{band}.band"] = Fact(band, "computed:band")
                facts[f"ranking.{band}.count"] = Fact(len(r.entries), scoring_src)
            for e in r.entries:
                facts[f"{prefix}.{band}.{e.product_id}.rank"] = Fact(e.rank, scoring_src)
                if prefix == "ranking":
                    facts[f"ranking.{band}.{e.product_id}.score_per_10k"] = Fact(
                        _r1(e.score_per_10k_inr), scoring_src
                    )
    return facts


def subset_for(
    facts: Facts, product_ids: tuple[str, ...], band: int | None
) -> dict[str, FactValue]:
    """The slice of the fact table a writer needs for one brief (sorted, values only)."""
    keep: dict[str, FactValue] = {}
    for ref, fact in sorted(facts.items()):
        pid = product_id_of(ref)
        parts = ref.split(".")
        if parts[0] == "meta":
            keep[ref] = fact.value
        elif pid is not None and pid in product_ids:
            if (
                parts[0] in ("ranking", "ranking_prev")
                and band is not None
                and parts[1] != str(band)
            ):
                continue
            keep[ref] = fact.value
        elif pid is None and parts[0] == "ranking" and band is not None and parts[1] == str(band):
            keep[ref] = fact.value
    return keep
