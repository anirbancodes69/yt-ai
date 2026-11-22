"""Settings: config/settings.yaml, overridden by environment (.env)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

Lang = Literal["hi", "en"]
SUPPORTED_LANGS: tuple[str, ...] = ("hi", "en")

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Paths(_Cfg):
    products: Path
    prices: Path
    scoring: Path
    formats: Path
    factcheck: Path
    script_text: Path
    out: Path
    cache: Path
    db: Path


class Budget(_Cfg):
    monthly_inr: float = Field(gt=0)
    per_video_inr: float = Field(gt=0)
    usd_to_inr: float = Field(gt=0)


class LLM(_Cfg):
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens: int = Field(gt=0)
    fix_attempts: int = Field(ge=0)


class Price(_Cfg):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)
    cache_write: float = Field(ge=0)


class Demo(_Cfg):
    as_of: date


class Settings(_Cfg):
    lang: Lang
    price_provider: str
    max_price_age_days: int = Field(gt=0)
    price_history_days: int = Field(gt=0)
    llm: LLM
    pricing_usd_per_mtok: dict[str, Price]
    paths: Paths
    budget: Budget
    demo: Demo

    def data_files(self) -> tuple[Path, ...]:
        """Files whose content a fact-check result depends on."""
        p = self.paths
        return (p.products, p.prices, p.scoring, p.formats, p.factcheck, p.script_text)

    def resolve(self, root: Path) -> Settings:
        """Return a copy with relative paths anchored at ``root``."""
        p = self.paths
        resolved = Paths(**{k: (v if v.is_absolute() else root / v) for k, v in p})
        return self.model_copy(update={"paths": resolved})


def resolve_lang(env: dict[str, str] | os._Environ[str], default: str) -> Lang:
    """CHANNEL_LANG wins; LANG is honoured only if it is exactly 'hi' or 'en'.

    Shells set LANG to locales like 'en_US.UTF-8', so we cannot treat any LANG
    value as a channel language.
    """
    explicit = env.get("CHANNEL_LANG", "").strip()
    if explicit:
        if explicit not in SUPPORTED_LANGS:
            raise ValueError(f"CHANNEL_LANG={explicit!r} not in {SUPPORTED_LANGS}")
        return explicit  # type: ignore[return-value]
    shell_lang = env.get("LANG", "").strip()
    if shell_lang in SUPPORTED_LANGS:
        return shell_lang  # type: ignore[return-value]
    return default  # type: ignore[return-value]


def load_settings(root: Path = REPO_ROOT, *, env: dict[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv(root / ".env", override=False)
        env = dict(os.environ)
    raw = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["lang"] = resolve_lang(env, raw.get("lang", "hi"))
    if env.get("CHANNEL_LLM_MODEL"):
        raw["llm"]["model"] = env["CHANNEL_LLM_MODEL"]
    return Settings.model_validate(raw).resolve(root)
