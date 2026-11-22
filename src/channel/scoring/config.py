"""Typed model for config/scoring.yaml, with weight-sum validation."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from channel.data.specs import SPEC_DEFS

_TOL = 1e-9


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metric(_Cfg):
    spec: str
    method: Literal["linear", "map", "bool"]
    weight: float = Field(gt=0, le=1)
    why: str
    min: float | None = None
    max: float | None = None
    higher_is_better: bool = True
    map: dict[str, float] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.spec not in SPEC_DEFS:
            raise ValueError(f"metric uses unknown spec {self.spec!r}")
        spec_type = SPEC_DEFS[self.spec].type
        if self.method == "linear":
            if self.min is None or self.max is None or self.max <= self.min:
                raise ValueError(f"{self.spec}: linear metric needs min < max")
            if spec_type not in ("int", "float"):
                raise ValueError(f"{self.spec}: linear needs a numeric spec, got {spec_type}")
        elif self.method == "map":
            if not self.map:
                raise ValueError(f"{self.spec}: map metric needs a non-empty map")
            if any(not 0 <= v <= 1 for v in self.map.values()):
                raise ValueError(f"{self.spec}: map values must be in [0, 1]")
            if any(k != k.lower() for k in self.map):
                raise ValueError(f"{self.spec}: map keys must be lower-case")
        elif self.method == "bool" and spec_type != "bool":
            raise ValueError(f"{self.spec}: bool metric needs a bool spec, got {spec_type}")
        return self


class Category(_Cfg):
    weight: float = Field(gt=0, le=1)
    label: dict[str, str]
    metrics: tuple[Metric, ...]

    @model_validator(mode="after")
    def _weights(self) -> Self:
        total = math.fsum(m.weight for m in self.metrics)
        if abs(total - 1) > _TOL:
            raise ValueError(f"metric weights sum to {total}, expected 1")
        return self


class Ranking(_Cfg):
    strength_threshold: float
    weakness_threshold: float
    max_strengths: int = Field(ge=0)
    max_weaknesses: int = Field(ge=0)
    tie_margin: float = Field(ge=0)


class ScoringConfig(_Cfg):
    version: int
    scale: float = Field(gt=0)
    ranking: Ranking
    categories: dict[str, Category]
    fingerprint: str = ""

    @model_validator(mode="after")
    def _weights(self) -> Self:
        total = math.fsum(c.weight for c in self.categories.values())
        if abs(total - 1) > _TOL:
            raise ValueError(f"category weights sum to {total}, expected 1")
        if self.ranking.weakness_threshold >= self.ranking.strength_threshold:
            raise ValueError("weakness_threshold must be below strength_threshold")
        return self

    @property
    def required_specs(self) -> set[str]:
        return {m.spec for c in self.categories.values() for m in c.metrics}


def load_scoring_config(path: Path) -> ScoringConfig:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    raw["fingerprint"] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return ScoringConfig.model_validate(raw)
