"""Script data structures.

``WriterScript`` is what a writer (Claude or the template writer) produces and is
the JSON schema sent to the API. ``Script`` is the final artifact: writer
sections plus code-inserted system sections (disclosure, method, sample notice).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from channel.formats import FormatName

WriterKind = Literal["hook", "intro", "product", "comparison", "price", "verdict", "outro"]
SystemKind = Literal["disclosure", "method", "sample_notice"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_M):
    text: str  # verbatim span of the narration that states the fact (one number at most)
    ref: str  # fact-table key, e.g. products.tarang-t9.specs.battery_mah
    value: bool | int | float | str  # the value as stated


class WriterSection(_M):
    id: str
    kind: WriterKind
    heading: str
    narration: str
    product_id: str | None
    claims: list[Claim]


class WriterScript(_M):
    title: str
    sections: list[WriterSection]


class ScriptSection(_M):
    id: str
    kind: WriterKind | SystemKind
    source: Literal["writer", "system"]
    heading: str
    narration: str
    product_id: str | None = None
    claims: list[Claim] = []


class Brief(_M):
    """What one video is about. Rebuilt independently by the fact-checker."""

    format: FormatName
    lang: Literal["hi", "en"]
    as_of: date
    params: dict[str, str | int]  # CLI inputs: band / product / a / b
    band_inr: int | None
    product_ids: tuple[str, ...]  # display order
    verdict_product_id: str | None  # who the verdict must name (None = tie)
    orientation: Literal["landscape", "portrait"]
    target_words: tuple[int, int]

    @property
    def video_id(self) -> str:
        bits = [self.format, *(str(self.params[k]) for k in sorted(self.params))]
        return "-".join([*bits, self.as_of.isoformat(), self.lang])


class Script(_M):
    video_id: str
    title: str
    writer: str  # "template" or "llm:<model>"
    sample: bool
    data_fingerprint: str
    brief: Brief
    sections: list[ScriptSection]

    def canonical_json(self) -> str:
        return self.model_dump_json()

    @property
    def narration_words(self) -> int:
        return sum(len(s.narration.split()) for s in self.sections)
