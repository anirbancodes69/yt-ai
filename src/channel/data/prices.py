"""Price providers.

Prices only ever enter the system through a ``PriceProvider``. We do NOT
scrape Amazon or Flipkart. The manual CSV provider is the working
implementation; the affiliate-API providers are clearly marked stubs.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from channel.data.models import PricePoint

CSV_COLUMNS = (
    "product_id",
    "retailer",
    "price_inr",
    "mrp_inr",
    "observed_on",
    "source_url",
    "fetched_at",
    "sample",
)


class PriceProviderError(RuntimeError):
    pass


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def get_prices(self, product_ids: Iterable[str] | None = None) -> list[PricePoint]:
        """Return price observations, optionally filtered to ``product_ids``."""
        ...


class ManualCSVProvider:
    """Reads hand-maintained prices from a CSV file (see data/prices.csv)."""

    name = "manual_csv"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def get_prices(self, product_ids: Iterable[str] | None = None) -> list[PricePoint]:
        wanted = set(product_ids) if product_ids is not None else None
        if not self.path.exists():
            raise PriceProviderError(f"price file not found: {self.path}")
        out: list[PricePoint] = []
        with self.path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            missing = set(CSV_COLUMNS) - set(reader.fieldnames or ())
            if missing:
                raise PriceProviderError(f"{self.path}: missing columns {sorted(missing)}")
            for lineno, row in enumerate(reader, start=2):
                if wanted is not None and row["product_id"] not in wanted:
                    continue
                try:
                    out.append(_row_to_price(row))
                except (ValidationError, ValueError) as exc:
                    raise PriceProviderError(f"{self.path}:{lineno}: {exc}") from exc
        return out


def _row_to_price(row: dict[str, str]) -> PricePoint:
    sample = row["sample"].strip().lower()
    if sample not in {"true", "false"}:
        raise ValueError(f"sample must be true/false, got {row['sample']!r}")
    return PricePoint.model_validate(
        {
            "product_id": row["product_id"].strip(),
            "retailer": row["retailer"].strip(),
            "price_inr": int(row["price_inr"]),
            "mrp_inr": int(row["mrp_inr"]) if row["mrp_inr"].strip() else None,
            "observed_on": row["observed_on"].strip(),
            "source_url": row["source_url"].strip(),
            "fetched_at": row["fetched_at"].strip(),
            "sample": sample == "true",
        }
    )


class AmazonAssociatesProvider:
    """STUB — Amazon Associates Product Advertising API.

    Not implemented. Amazon only issues working API credentials to Associates
    accounts with qualifying sales, and the API's terms govern caching and
    display of prices. When implementing:

    * use the official API only (never scrape amazon.in),
    * cache responses (respect the API's max cache age for prices),
    * set ``source_url`` to the product's detail-page URL and ``fetched_at``
      to the API response time,
    * log the call in the cost ledger even though the API is free.
    """

    name = "amazon_associates"

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        partner_tag: str | None = None,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.partner_tag = partner_tag

    def get_prices(self, product_ids: Iterable[str] | None = None) -> list[PricePoint]:
        raise NotImplementedError(
            "AmazonAssociatesProvider is a stub. Use ManualCSVProvider until "
            "API access is approved (see docstring)."
        )


class FlipkartAffiliateProvider:
    """STUB — Flipkart Affiliate API (product feed / search).

    Not implemented. Needs an approved Flipkart Affiliate ID and token. Same
    rules as the Amazon stub: official API only, cache everything, populate
    ``source_url`` and ``fetched_at`` from the response.
    """

    name = "flipkart_affiliate"

    def __init__(self, affiliate_id: str | None = None, token: str | None = None) -> None:
        self.affiliate_id = affiliate_id
        self.token = token

    def get_prices(self, product_ids: Iterable[str] | None = None) -> list[PricePoint]:
        raise NotImplementedError(
            "FlipkartAffiliateProvider is a stub. Use ManualCSVProvider until "
            "API access is approved (see docstring)."
        )


def make_provider(name: str, *, csv_path: Path | str | None = None) -> PriceProvider:
    if name == ManualCSVProvider.name:
        if csv_path is None:
            raise PriceProviderError("manual_csv provider needs csv_path")
        return ManualCSVProvider(csv_path)
    if name == AmazonAssociatesProvider.name:
        return AmazonAssociatesProvider()
    if name == FlipkartAffiliateProvider.name:
        return FlipkartAffiliateProvider()
    raise PriceProviderError(f"unknown price provider {name!r}")
