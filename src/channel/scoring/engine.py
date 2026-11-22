"""Deterministic, explainable scoring.

Every number in a ProductScore can be traced back to (a) a spec value with its
``data_ref`` and ``source_url`` and (b) a rule in config/scoring.yaml. No
randomness, no dataset-relative normalisation, no LLM.
"""

from __future__ import annotations

import math
from datetime import date

from pydantic import BaseModel, ConfigDict

from channel.data.models import Dataset, PricePoint, Product
from channel.data.pricing import current_price
from channel.scoring.config import Metric, ScoringConfig


class ScoringError(ValueError):
    pass


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class MetricScore(_Out):
    spec: str
    data_ref: str
    raw_value: bool | int | float | str
    source_url: str
    method: str
    normalized: float  # 0..1
    weight: float  # within category
    why: str


class CategoryScore(_Out):
    name: str
    label: str
    weight: float  # within overall
    score: float  # 0..scale
    contribution: float  # score * weight, sums to overall
    metrics: tuple[MetricScore, ...]


class ProductScore(_Out):
    product_id: str
    display_name: str
    overall: float
    categories: tuple[CategoryScore, ...]
    strengths: tuple[str, ...]  # category names, best first
    weaknesses: tuple[str, ...]  # category names, worst first
    scoring_version: int
    scoring_fingerprint: str

    def category(self, name: str) -> CategoryScore:
        for c in self.categories:
            if c.name == name:
                return c
        raise KeyError(name)


def data_ref(product_id: str, spec: str) -> str:
    return f"products.{product_id}.specs.{spec}"


def _normalize(metric: Metric, value: bool | int | float | str, product_id: str) -> float:
    if metric.method == "bool":
        if not isinstance(value, bool):
            raise ScoringError(f"{product_id}.{metric.spec}: expected bool, got {value!r}")
        return 1.0 if value else 0.0
    if metric.method == "map":
        assert metric.map is not None
        key = str(value).strip().lower()
        if key not in metric.map:
            raise ScoringError(
                f"{product_id}.{metric.spec}: value {value!r} not in scoring map "
                f"{sorted(metric.map)}; add it to config/scoring.yaml"
            )
        return metric.map[key]
    # linear
    assert metric.min is not None and metric.max is not None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoringError(f"{product_id}.{metric.spec}: expected number, got {value!r}")
    x = (float(value) - metric.min) / (metric.max - metric.min)
    x = min(1.0, max(0.0, x))
    return x if metric.higher_is_better else 1.0 - x


def score_product(product: Product, cfg: ScoringConfig, *, lang: str = "hi") -> ProductScore:
    missing = cfg.required_specs - product.specs.keys()
    if missing:
        raise ScoringError(f"{product.id}: missing specs required by scoring: {sorted(missing)}")

    categories: list[CategoryScore] = []
    for name, cat in cfg.categories.items():
        metrics = []
        for m in cat.metrics:
            spec = product.specs[m.spec]
            metrics.append(
                MetricScore(
                    spec=m.spec,
                    data_ref=data_ref(product.id, m.spec),
                    raw_value=spec.value,
                    source_url=spec.source_url,
                    method=m.method,
                    normalized=_normalize(m, spec.value, product.id),
                    weight=m.weight,
                    why=m.why,
                )
            )
        score = math.fsum(ms.normalized * ms.weight for ms in metrics) * cfg.scale
        categories.append(
            CategoryScore(
                name=name,
                label=cat.label.get(lang, name),
                weight=cat.weight,
                score=score,
                contribution=score * cat.weight,
                metrics=tuple(metrics),
            )
        )

    overall = math.fsum(c.contribution for c in categories)
    r = cfg.ranking
    # Sort keys include the name so equal scores order deterministically.
    strengths = sorted(
        (c for c in categories if c.score >= r.strength_threshold),
        key=lambda c: (-c.score, c.name),
    )[: r.max_strengths]
    weaknesses = sorted(
        (c for c in categories if c.score <= r.weakness_threshold),
        key=lambda c: (c.score, c.name),
    )[: r.max_weaknesses]

    return ProductScore(
        product_id=product.id,
        display_name=product.display_name,
        overall=overall,
        categories=tuple(categories),
        strengths=tuple(c.name for c in strengths),
        weaknesses=tuple(c.name for c in weaknesses),
        scoring_version=cfg.version,
        scoring_fingerprint=cfg.fingerprint,
    )


# --------------------------------------------------------------------------- ranking


class RankedEntry(_Out):
    rank: int
    product_id: str
    display_name: str
    overall: float
    price: PricePoint
    score_per_10k_inr: float
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]


class Exclusion(_Out):
    product_id: str
    reason: str


class BandRanking(_Out):
    band_inr: int
    as_of: date
    sample: bool
    entries: tuple[RankedEntry, ...]
    excluded: tuple[Exclusion, ...]
    scoring_version: int
    scoring_fingerprint: str

    @property
    def winner(self) -> RankedEntry | None:
        return self.entries[0] if self.entries else None


def rank_band(
    dataset: Dataset,
    cfg: ScoringConfig,
    *,
    band_inr: int,
    as_of: date,
    max_price_age_days: int,
    lang: str = "hi",
) -> BandRanking:
    """Rank every product whose current price is <= ``band_inr``.

    Order: overall score desc, then cheaper first, then product id.
    Products without a fresh price, or above the band, are listed in
    ``excluded`` with the reason, so nothing disappears silently.
    """
    candidates: list[tuple[ProductScore, PricePoint]] = []
    excluded: list[Exclusion] = []
    for pid in sorted(dataset.products):
        product = dataset.products[pid]
        price = current_price(dataset.prices, pid, as_of=as_of, max_age_days=max_price_age_days)
        if price is None:
            excluded.append(
                Exclusion(product_id=pid, reason=f"no price within {max_price_age_days} days")
            )
            continue
        if price.price_inr > band_inr:
            excluded.append(
                Exclusion(product_id=pid, reason=f"price {price.price_inr} > band {band_inr}")
            )
            continue
        candidates.append((score_product(product, cfg, lang=lang), price))

    candidates.sort(key=lambda sp: (-sp[0].overall, sp[1].price_inr, sp[0].product_id))
    entries = tuple(
        RankedEntry(
            rank=i,
            product_id=s.product_id,
            display_name=s.display_name,
            overall=s.overall,
            price=p,
            score_per_10k_inr=s.overall / (p.price_inr / 10_000),
            strengths=s.strengths,
            weaknesses=s.weaknesses,
        )
        for i, (s, p) in enumerate(candidates, start=1)
    )
    return BandRanking(
        band_inr=band_inr,
        as_of=as_of,
        sample=dataset.is_sample,
        entries=entries,
        excluded=tuple(excluded),
        scoring_version=cfg.version,
        scoring_fingerprint=cfg.fingerprint,
    )


# ------------------------------------------------------------------------ head to head


class CategoryVerdict(_Out):
    name: str
    label: str
    a_score: float
    b_score: float
    winner: str | None  # product id, or None for a tie


class Comparison(_Out):
    a: ProductScore
    b: ProductScore
    categories: tuple[CategoryVerdict, ...]
    overall_winner: str | None
    tie_margin: float


def compare(a: Product, b: Product, cfg: ScoringConfig, *, lang: str = "hi") -> Comparison:
    if a.id == b.id:
        raise ScoringError("cannot compare a product with itself")
    sa, sb = score_product(a, cfg, lang=lang), score_product(b, cfg, lang=lang)
    margin = cfg.ranking.tie_margin

    def pick(x: float, y: float) -> str | None:
        if abs(x - y) < margin:
            return None
        return a.id if x > y else b.id

    verdicts = tuple(
        CategoryVerdict(
            name=ca.name,
            label=ca.label,
            a_score=ca.score,
            b_score=cb.score,
            winner=pick(ca.score, cb.score),
        )
        for ca, cb in zip(sa.categories, sb.categories, strict=True)
    )
    return Comparison(
        a=sa,
        b=sb,
        categories=verdicts,
        overall_winner=pick(sa.overall, sb.overall),
        tie_margin=margin,
    )
