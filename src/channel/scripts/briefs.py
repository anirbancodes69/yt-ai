"""Turn a format + inputs into a Brief (which products, who the verdict names).

Pure functions of the data, so the fact-checker can rebuild the brief and
confirm the script covers what it should.
"""

from __future__ import annotations

from datetime import date, timedelta

from channel.data.models import Dataset
from channel.data.pricing import current_price
from channel.formats import FormatsConfig
from channel.scoring import ScoringConfig, compare, rank_band
from channel.scripts.models import Brief


class BriefError(ValueError):
    pass


def _band_for(price: int, bands: tuple[int, ...]) -> int:
    for b in sorted(bands):
        if price <= b:
            return b
    raise BriefError(f"price {price} is above the highest band {max(bands)}")


def build_brief(
    fmt: str,
    params: dict[str, str | int],
    *,
    dataset: Dataset,
    cfg: ScoringConfig,
    formats: FormatsConfig,
    as_of: date,
    lang: str,
    max_price_age_days: int,
) -> Brief:
    spec = formats.spec(fmt)

    def rank(band: int, when: date = as_of):
        return rank_band(
            dataset, cfg, band_inr=band, as_of=when, max_price_age_days=max_price_age_days
        )

    def price_of(pid: str) -> int:
        dataset.product(pid)  # raises a clear KeyError for unknown ids
        pp = current_price(dataset.prices, pid, as_of=as_of, max_age_days=max_price_age_days)
        if pp is None:
            raise BriefError(f"{pid}: no price within {max_price_age_days} days of {as_of}")
        return pp.price_inr

    band: int | None = None
    verdict: str | None
    if fmt in ("best_under", "monthly_refresh"):
        band = int(params["band"])
        if band not in formats.price_bands_inr:
            raise BriefError(f"band {band} not in {formats.price_bands_inr}")
        entries = rank(band).entries[: spec.max_products]
        if len(entries) < spec.min_products:
            raise BriefError(
                f"only {len(entries)} products under {band}; format needs {spec.min_products}"
            )
        products = tuple(e.product_id for e in entries)
        verdict = products[0]
        if (
            fmt == "monthly_refresh"
            and not rank(band, as_of - timedelta(spec.lookback_days)).entries
        ):
            raise BriefError(f"no ranking {spec.lookback_days} days ago to compare against")
    elif fmt == "head_to_head":
        a, b = str(params["a"]), str(params["b"])
        price_of(a), price_of(b)
        products = (a, b)
        verdict = compare(dataset.product(a), dataset.product(b), cfg).overall_winner
    elif fmt == "launch_explainer":
        pid = str(params["product"])
        band = _band_for(price_of(pid), formats.price_bands_inr)
        rivals = [e.product_id for e in rank(band).entries if e.product_id != pid][: spec.rivals]
        products = (pid, *rivals)
        verdict = pid
    elif fmt == "deal_short":
        pid = str(params["product"])
        current = price_of(pid)
        band = _band_for(current, formats.price_bands_inr)
        products = (pid,)
        verdict = pid
    else:
        raise BriefError(f"unknown format {fmt!r}")

    return Brief(
        format=fmt,  # type: ignore[arg-type]
        lang=lang,  # type: ignore[arg-type]
        as_of=as_of,
        params=dict(params),
        band_inr=band,
        product_ids=products,
        verdict_product_id=verdict,
        orientation=spec.orientation,
        target_words=spec.target_words,
    )
