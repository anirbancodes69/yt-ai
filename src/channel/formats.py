"""Typed model for config/formats.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

FormatName = Literal[
    "best_under", "head_to_head", "launch_explainer", "deal_short", "monthly_refresh"
]
FORMAT_NAMES: tuple[str, ...] = (
    "best_under",
    "head_to_head",
    "launch_explainer",
    "deal_short",
    "monthly_refresh",
)


class FormatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    orientation: Literal["landscape", "portrait"]
    target_words: tuple[int, int]
    target_seconds: tuple[float, float]
    min_products: int = Field(default=1, ge=1)
    max_products: int = Field(default=6, ge=1)
    rivals: int = Field(default=2, ge=0)
    lookback_days: int = Field(default=30, gt=0)


class FormatsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price_bands_inr: tuple[int, ...]
    words_per_minute: dict[str, int]
    formats: dict[FormatName, FormatSpec]

    def spec(self, name: str) -> FormatSpec:
        return self.formats[name]  # type: ignore[index]


def load_formats(path: Path) -> FormatsConfig:
    return FormatsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
