"""Price selection rules: what counts as a product's current price, and its history."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from channel.data.models import PricePoint


@dataclass(frozen=True)
class DailyPrice:
    """Lowest observed price across retailers on one day (for charts)."""

    on: date
    price_inr: int
    source: PricePoint


def current_price(
    prices: Iterable[PricePoint],
    product_id: str,
    *,
    as_of: date,
    max_age_days: int,
) -> PricePoint | None:
    """Current price = lowest of each retailer's latest observation that is

    * on or before ``as_of`` (no peeking into the future), and
    * no older than ``max_age_days`` (stale prices are not quoted).

    Ties on price break on retailer name so the result is deterministic.
    Returns ``None`` if there is no fresh price.
    """
    cutoff = as_of - timedelta(days=max_age_days)
    latest: dict[str, PricePoint] = {}
    for pp in prices:
        if pp.product_id != product_id or pp.observed_on > as_of or pp.observed_on < cutoff:
            continue
        prev = latest.get(pp.retailer)
        if prev is None or pp.observed_on > prev.observed_on:
            latest[pp.retailer] = pp
    if not latest:
        return None
    return min(latest.values(), key=lambda p: (p.price_inr, p.retailer))


def price_history(
    prices: Iterable[PricePoint], product_id: str, *, as_of: date | None = None
) -> list[DailyPrice]:
    """Daily lowest price across retailers, sorted by date."""
    by_day: dict[date, list[PricePoint]] = defaultdict(list)
    for pp in prices:
        if pp.product_id == product_id and (as_of is None or pp.observed_on <= as_of):
            by_day[pp.observed_on].append(pp)
    out = []
    for day in sorted(by_day):
        best = min(by_day[day], key=lambda p: (p.price_inr, p.retailer))
        out.append(DailyPrice(on=day, price_inr=best.price_inr, source=best))
    return out
