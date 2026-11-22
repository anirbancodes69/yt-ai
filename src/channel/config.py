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
    render_text: Path
    packaging: Path
    out: Path
    cache: Path
    db: Path
    press: Path
    music: Path
    fonts: Path


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


class GoogleTTS(_Cfg):
    voices: dict[str, str]
    speaking_rate: float = Field(gt=0)


class EspeakTTS(_Cfg):
    voices: dict[str, str]
    wpm: int = Field(gt=0)


class TTSPrice(_Cfg):
    usd_per_mchar: float = Field(ge=0)
    free_chars_per_month: int = Field(ge=0)


class TTS(_Cfg):
    provider: Literal["google", "espeak"]
    google: GoogleTTS
    espeak: EspeakTTS
    pricing: dict[str, TTSPrice]


class Render(_Cfg):
    fps: int = Field(gt=0)
    sentence_gap_s: float = Field(ge=0)
    section_gap_s: float = Field(ge=0)
    lead_in_s: float = Field(ge=0)
    tail_s: float = Field(ge=0)
    anim_frames: int = Field(ge=1)
    crossfade_frames: int = Field(ge=0)
    music_volume_db: float
    x264_preset: str
    crf: int = Field(ge=0, le=51)
    scale: float = Field(gt=0, le=1)
    max_speedup: float = Field(ge=1)
    max_slowdown: float = Field(gt=0, le=1)


class Demo(_Cfg):
    as_of: date


class Settings(_Cfg):
    lang: Lang
    price_provider: str
    max_price_age_days: int = Field(gt=0)
    price_history_days: int = Field(gt=0)
    llm: LLM
    pricing_usd_per_mtok: dict[str, Price]
    tts: TTS
    render: Render
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
