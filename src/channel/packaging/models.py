"""Package data structures and config model (config/packaging.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from channel.formats import FormatName


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chapter(_M):
    start_s: int
    title: str


class Link(_M):
    product_id: str
    retailer: str
    label: str
    url: str
    affiliate: bool  # an affiliate parameter was added


class Package(_M):
    video_id: str
    format: FormatName
    lang: Literal["hi", "en"]
    sample: bool
    orientation: Literal["landscape", "portrait"]
    preview: bool = False  # built from a brief only (no script/render): not uploadable
    title: str
    title_variants: list[str]
    description: str
    tags: list[str]
    hashtags: list[str]
    chapters: list[Chapter]
    links: list[Link]
    category_id: int
    default_language: str
    made_for_kids: bool
    contains_synthetic_media: bool
    thumbnail: str  # file name inside the package folder
    script_sha256: str = ""
    data_fingerprint: str = ""
    warnings: list[str] = Field(default_factory=list)


class RetailerCfg(_M):
    label: str
    param: str
    env: str
    sample_value: str | None = None


class Limits(_M):
    title_chars: int
    title_soft_chars: int
    description_bytes: int
    tags_chars: int
    tag_chars: int
    hashtags_max: int
    chapter_min_seconds: int
    chapter_min_count: int
    thumbnail_bytes: int


class LangText(_M):
    disclosure: str
    sample_notice: str
    title_sample_prefix: str
    links_heading: str
    link_line: str
    link_line_no_price: str
    chapters_heading: str
    price_as_of: str
    price_note: str
    chapter_intro: str
    titles: dict[FormatName, list[str]]
    summaries: dict[FormatName, str]
    verdict_tie: str
    hashtags: dict[FormatName, list[str]]
    tags: dict[str, list[str]]
    thumbnail: dict[str, str]


class PackagingConfig(_M):
    category_id: int
    made_for_kids: bool
    contains_synthetic_media: bool
    limits: Limits
    affiliate: dict[str, dict[str, RetailerCfg]]
    hi: LangText
    en: LangText

    def lang(self, lang: str) -> LangText:
        return self.hi if lang == "hi" else self.en

    @property
    def retailers(self) -> dict[str, RetailerCfg]:
        return self.affiliate["retailers"]


def load_packaging(path: Path) -> PackagingConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    for lang in ("hi", "en"):  # YAML folds ">-" blocks already; strip stray whitespace
        raw[lang]["disclosure"] = " ".join(raw[lang]["disclosure"].split())
    return PackagingConfig.model_validate(raw)
