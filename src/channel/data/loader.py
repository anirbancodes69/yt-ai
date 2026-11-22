"""Load and cross-validate products + prices into a Dataset."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from channel.data.models import Dataset, Product
from channel.data.prices import PriceProvider


class DataError(RuntimeError):
    pass


def load_products(path: Path) -> dict[str, Product]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "products" not in raw:
        raise DataError(f"{path}: expected a top-level 'products:' list")
    products: dict[str, Product] = {}
    for i, item in enumerate(raw["products"]):
        try:
            product = Product.model_validate(item)
        except ValidationError as exc:
            ident = item.get("id", f"#{i}") if isinstance(item, dict) else f"#{i}"
            raise DataError(f"{path}: product {ident}: {exc}") from exc
        if product.id in products:
            raise DataError(f"{path}: duplicate product id {product.id!r}")
        products[product.id] = product
    return products


def load_dataset(products_path: Path, provider: PriceProvider) -> Dataset:
    products = load_products(products_path)
    prices = provider.get_prices(products.keys())
    try:
        return Dataset(products=products, prices=tuple(prices))
    except ValidationError as exc:
        raise DataError(str(exc)) from exc
