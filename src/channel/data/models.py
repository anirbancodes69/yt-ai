"""Core data models. Every fact carries ``source_url`` and ``fetched_at``.

These models are the single source of truth for anything a video says. The
fact-checker (Phase 2) resolves script claims against them by path, e.g.
``products.zentra-z5.specs.battery_mah``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from channel.data.specs import SPEC_DEFS, check_value_type

SAMPLE_URL_HOST = "example.com"


def _check_url(v: str) -> str:
    if not (v.startswith("https://") or v.startswith("http://")):
        raise ValueError(f"source_url must be an http(s) URL, got {v!r}")
    return v


def _check_aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("fetched_at must include a timezone (e.g. 2026-11-20T10:00:00+05:30)")
    return v


SourceUrl = Annotated[str, AfterValidator(_check_url)]
AwareDatetime = Annotated[datetime, AfterValidator(_check_aware)]
Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Sourced(_Strict):
    """Mixin: where a fact came from and when we fetched it."""

    source_url: SourceUrl
    fetched_at: AwareDatetime

    @field_validator("fetched_at")
    @classmethod
    def _not_in_future(cls, v: datetime) -> datetime:
        if v > datetime.now(UTC):
            raise ValueError(f"fetched_at {v.isoformat()} is in the future")
        return v


class Spec(Sourced):
    value: bool | int | float | str
    unit: str | None = None


class LaunchInfo(Sourced):
    launched_on: date
    price_inr: Annotated[int, Field(gt=0)]


class Product(_Strict):
    id: Slug
    brand: str
    model: str
    category: str = "phone"
    sample: bool
    launch: LaunchInfo
    specs: dict[str, Spec]
    press_images: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        name = f"{self.brand} {self.model}"
        return f"[SAMPLE] {name}" if self.sample else name

    def spec_value(self, key: str) -> bool | int | float | str:
        if key not in self.specs:
            raise KeyError(f"{self.id}: missing spec {key!r}")
        return self.specs[key].value

    @model_validator(mode="after")
    def _check_specs(self) -> Self:
        for key, spec in self.specs.items():
            check_value_type(key, spec.value)
            registered_unit = SPEC_DEFS[key].unit
            if spec.unit is not None and spec.unit != registered_unit:
                raise ValueError(
                    f"{self.id}: spec {key!r} unit {spec.unit!r} != registry unit "
                    f"{registered_unit!r}"
                )
        return self

    @model_validator(mode="after")
    def _sample_urls(self) -> Self:
        _check_sample_url(self.sample, self.launch.source_url, self.id)
        for spec in self.specs.values():
            _check_sample_url(self.sample, spec.source_url, self.id)
        return self


class PricePoint(Sourced):
    product_id: Slug
    retailer: str
    price_inr: Annotated[int, Field(gt=0)]
    mrp_inr: Annotated[int, Field(gt=0)] | None = None
    observed_on: date
    sample: bool

    @model_validator(mode="after")
    def _checks(self) -> Self:
        if self.mrp_inr is not None and self.mrp_inr < self.price_inr:
            raise ValueError(
                f"{self.product_id}@{self.retailer} {self.observed_on}: "
                f"price {self.price_inr} above MRP {self.mrp_inr}"
            )
        if self.observed_on > self.fetched_at.date():
            raise ValueError(
                f"{self.product_id}@{self.retailer}: observed_on {self.observed_on} "
                f"is after fetched_at {self.fetched_at.date()}"
            )
        _check_sample_url(self.sample, self.source_url, self.product_id)
        return self


def _check_sample_url(sample: bool, url: str, ident: str) -> None:
    """Sample facts must point at example.com; real facts must not.

    This makes it structurally impossible to pass invented data off as real
    (or to accidentally leave a placeholder URL in real data).
    """
    is_placeholder = f"://{SAMPLE_URL_HOST}/" in url
    if sample and not is_placeholder:
        raise ValueError(f"{ident}: SAMPLE record must use an {SAMPLE_URL_HOST} source_url")
    if not sample and is_placeholder:
        raise ValueError(f"{ident}: real record has placeholder source_url {url}")


class Dataset(_Strict):
    products: dict[str, Product]
    prices: tuple[PricePoint, ...]

    @property
    def is_sample(self) -> bool:
        return any(p.sample for p in self.products.values())

    def product(self, product_id: str) -> Product:
        try:
            return self.products[product_id]
        except KeyError:
            known = ", ".join(sorted(self.products))
            raise KeyError(f"unknown product {product_id!r}; known: {known}") from None

    @model_validator(mode="after")
    def _cross_checks(self) -> Self:
        for key, product in self.products.items():
            if key != product.id:
                raise ValueError(f"product key {key!r} != id {product.id!r}")
        flags = {p.sample for p in self.products.values()} | {p.sample for p in self.prices}
        if len(flags) > 1:
            raise ValueError("dataset mixes SAMPLE and real records; keep them in separate files")
        for pp in self.prices:
            if pp.product_id not in self.products:
                raise ValueError(f"price row references unknown product {pp.product_id!r}")
        seen: set[tuple[str, str, date]] = set()
        for pp in self.prices:
            k = (pp.product_id, pp.retailer, pp.observed_on)
            if k in seen:
                raise ValueError(f"duplicate price row {k}")
            seen.add(k)
        return self
