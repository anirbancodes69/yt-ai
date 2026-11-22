from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from channel.data import Dataset, PricePoint, Product, Spec
from tests.conftest import product_dict

URL = "https://example.com/sample/x"
TS = "2026-11-18T10:00:00+05:30"


def test_sample_dataset_loads(dataset: Dataset) -> None:
    assert len(dataset.products) == 8
    assert dataset.is_sample
    assert all(p.sample for p in dataset.products.values())
    assert all(pp.sample for pp in dataset.prices)


def test_every_fact_is_sourced(dataset: Dataset) -> None:
    for p in dataset.products.values():
        assert p.launch.source_url and p.launch.fetched_at.tzinfo
        for spec in p.specs.values():
            assert spec.source_url.startswith("https://")
            assert spec.fetched_at.tzinfo is not None
    for pp in dataset.prices:
        assert pp.source_url.startswith("https://") and pp.fetched_at.tzinfo


def test_display_name_is_labelled_sample(dataset: Dataset) -> None:
    assert dataset.product("tarang-t9").display_name == "[SAMPLE] Tarang T9"


def test_spec_requires_source_url() -> None:
    with pytest.raises(ValidationError, match="source_url"):
        Spec.model_validate({"value": 5000, "fetched_at": TS})


def test_spec_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Spec.model_validate({"value": 5000, "source_url": URL, "fetched_at": "2026-11-18T10:00"})


def test_fetched_at_cannot_be_in_future() -> None:
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    with pytest.raises(ValidationError, match="future"):
        Spec.model_validate({"value": 1, "source_url": URL, "fetched_at": future})


def test_non_http_url_rejected() -> None:
    with pytest.raises(ValidationError, match="http"):
        Spec.model_validate({"value": 1, "source_url": "file:///etc", "fetched_at": TS})


def test_unknown_spec_key_rejected(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["specs"]["batery_mah"] = d["specs"].pop("battery_mah")
    with pytest.raises(ValidationError, match="unknown spec key 'batery_mah'"):
        Product.model_validate(d)


def test_wrong_spec_type_rejected(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["specs"]["battery_mah"]["value"] = "5000"
    with pytest.raises(ValidationError, match="expects int"):
        Product.model_validate(d)


def test_bool_is_not_accepted_as_int(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["specs"]["ram_gb"]["value"] = True
    with pytest.raises(ValidationError, match="expects int"):
        Product.model_validate(d)


def test_wrong_unit_rejected(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["specs"]["battery_mah"]["unit"] = "Wh"
    with pytest.raises(ValidationError, match="unit"):
        Product.model_validate(d)


def test_sample_record_must_use_placeholder_url(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["specs"]["battery_mah"]["source_url"] = "https://www.gsmarena.com/x"
    with pytest.raises(ValidationError, match="SAMPLE record"):
        Product.model_validate(d)


def test_real_record_cannot_use_placeholder_url(dataset: Dataset) -> None:
    d = product_dict(dataset)
    d["sample"] = False
    with pytest.raises(ValidationError, match="placeholder"):
        Product.model_validate(d)


def _price(**kw: object) -> dict[str, object]:
    base = {
        "product_id": "tarang-t9",
        "retailer": "sample-store-a",
        "price_inr": 18999,
        "mrp_inr": 21999,
        "observed_on": "2026-11-18",
        "source_url": URL,
        "fetched_at": TS,
        "sample": True,
    }
    return base | kw


def test_price_above_mrp_rejected() -> None:
    with pytest.raises(ValidationError, match="above MRP"):
        PricePoint.model_validate(_price(price_inr=30000))


def test_price_observed_after_fetch_rejected() -> None:
    with pytest.raises(ValidationError, match="after fetched_at"):
        PricePoint.model_validate(_price(observed_on="2026-11-19"))


def test_non_positive_price_rejected() -> None:
    with pytest.raises(ValidationError):
        PricePoint.model_validate(_price(price_inr=0))


def test_dataset_rejects_unknown_product_price(dataset: Dataset) -> None:
    bad = PricePoint.model_validate(_price(product_id="ghost-phone"))
    with pytest.raises(ValidationError, match="unknown product"):
        Dataset(products=dataset.products, prices=(*dataset.prices, bad))


def test_dataset_rejects_duplicate_price_rows(dataset: Dataset) -> None:
    with pytest.raises(ValidationError, match="duplicate price row"):
        Dataset(products=dataset.products, prices=(*dataset.prices, dataset.prices[0]))


def test_dataset_rejects_mixing_sample_and_real(dataset: Dataset) -> None:
    real = PricePoint.model_validate(
        _price(sample=False, source_url="https://shop.invalid/p", observed_on="2026-11-17")
    )
    with pytest.raises(ValidationError, match="mixes SAMPLE and real"):
        Dataset(products=dataset.products, prices=(*dataset.prices, real))


def test_unknown_product_lookup_lists_known(dataset: Dataset) -> None:
    with pytest.raises(KeyError, match="tarang-t9"):
        dataset.product("nope")
