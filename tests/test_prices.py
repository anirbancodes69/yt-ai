from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from channel.data import (
    AmazonAssociatesProvider,
    Dataset,
    FlipkartAffiliateProvider,
    ManualCSVProvider,
    PriceProvider,
    PriceProviderError,
    current_price,
    make_provider,
    price_history,
)
from channel.data.prices import CSV_COLUMNS

AS_OF = date(2026, 11, 20)


def test_csv_provider_reads_all_rows(settings) -> None:
    rows = ManualCSVProvider(settings.paths.prices).get_prices()
    assert len(rows) == 208


def test_csv_provider_filters_by_product(settings) -> None:
    rows = ManualCSVProvider(settings.paths.prices).get_prices(["ostra-o2"])
    assert rows and {r.product_id for r in rows} == {"ostra-o2"}


def test_csv_missing_column(tmp_path: Path) -> None:
    f = tmp_path / "p.csv"
    f.write_text("product_id,retailer\nx,y\n")
    with pytest.raises(PriceProviderError, match="missing columns"):
        ManualCSVProvider(f).get_prices()


def test_csv_bad_row_reports_line(tmp_path: Path) -> None:
    f = tmp_path / "p.csv"
    f.write_text(
        ",".join(CSV_COLUMNS) + "\n"
        "a,s,-5,,2026-11-01,https://example.com/sample/a,2026-11-01T10:00:00+05:30,true\n"
    )
    with pytest.raises(PriceProviderError, match=r"p\.csv:2"):
        ManualCSVProvider(f).get_prices()


def test_csv_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PriceProviderError, match="not found"):
        ManualCSVProvider(tmp_path / "nope.csv").get_prices()


@pytest.mark.parametrize("cls", [AmazonAssociatesProvider, FlipkartAffiliateProvider])
def test_affiliate_providers_are_stubs(cls) -> None:
    provider = cls()
    assert isinstance(provider, PriceProvider)
    with pytest.raises(NotImplementedError, match="stub"):
        provider.get_prices()


def test_make_provider(settings) -> None:
    assert isinstance(
        make_provider("manual_csv", csv_path=settings.paths.prices), ManualCSVProvider
    )
    with pytest.raises(PriceProviderError):
        make_provider("scrape_amazon")


def test_current_price_is_lowest_latest(dataset: Dataset) -> None:
    pp = current_price(dataset.prices, "tarang-t9", as_of=AS_OF, max_age_days=7)
    assert pp is not None
    assert (pp.price_inr, pp.retailer, pp.observed_on) == (18999, "sample-store-a", AS_OF)


def test_current_price_ignores_future_observations(dataset: Dataset) -> None:
    pp = current_price(dataset.prices, "ostra-o2", as_of=date(2026, 11, 1), max_age_days=7)
    assert pp is not None and pp.price_inr == 22999  # before the drop on 2026-11-06


def test_stale_price_is_not_quoted(dataset: Dataset) -> None:
    assert (
        current_price(dataset.prices, "tarang-t9", as_of=date(2026, 12, 31), max_age_days=7) is None
    )


def test_price_history_shows_drop(dataset: Dataset) -> None:
    hist = price_history(dataset.prices, "ostra-o2", as_of=AS_OF)
    assert [h.on for h in hist] == sorted(h.on for h in hist)
    assert hist[0].price_inr == 22999 and hist[-1].price_inr == 19999
    assert all(h.source.retailer == "sample-store-a" for h in hist)  # store A is cheaper


def test_price_history_starts_at_launch(dataset: Dataset) -> None:
    hist = price_history(dataset.prices, "zentra-z8-pro")
    assert hist[0].on >= dataset.product("zentra-z8-pro").launch.launched_on
