"""Plan which visual is on screen when, from the timed sentences and their claims.

Within a product section each sentence picks a visual by what it cites:
price-history refs → price chart, category specs/scores → scorecard, rank /
current price / overall → product card; sentences after the last claim
(who should buy / skip) → buy-if/skip-if card. Consecutive sentences with the
same visual merge into one shot. Shots tile the whole timeline with no gaps.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from typing import Any

from channel.scripts.models import Script, ScriptSection
from channel.voice.narration import Timeline

SPEC_CATEGORY = {
    "chipset": "performance",
    "antutu_v10": "performance",
    "ram_gb": "performance",
    "storage_gb": "performance",
    "display_size_in": "display",
    "display_type": "display",
    "refresh_rate_hz": "display",
    "peak_brightness_nits": "display",
    "main_camera_mp": "camera",
    "main_camera_ois": "camera",
    "ultrawide_mp": "camera",
    "front_camera_mp": "camera",
    "battery_mah": "battery",
    "charging_w": "battery",
    "os_updates_years": "software",
    "security_updates_years": "software",
    "ip_rating": "build",
    "weight_g": "build",
}


@dataclass
class Shot:
    kind: (
        str  # title|notice|weights|product|scorecard|price_chart|buyskip|ranking|compare|deal|outro
    )
    section_id: str
    start: float
    end: float
    product_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    animate: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    def same_visual(self, other: Shot) -> bool:
        return (self.kind, self.product_id, self.params) == (
            other.kind,
            other.product_id,
            other.params,
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _category_of(ref: str) -> str | None:
    parts = ref.split(".")
    if parts[0] == "products" and len(parts) == 4 and parts[2] == "specs":
        return SPEC_CATEGORY.get(parts[3])
    if parts[0] == "scores" and len(parts) == 3 and parts[2] != "overall":
        return parts[2]
    return None


def _sentence_visual(refs: list[str]) -> tuple[str, dict[str, Any]] | None:
    if any(r.split(".")[-1] in ("lowest_90d", "highest_90d", "drop_from_high_90d") for r in refs):
        return "price_chart", {}
    cats = [c for c in (_category_of(r) for r in refs) if c]
    if cats:
        return "scorecard", {"highlight": cats[0]}
    if refs:
        return "product", {}
    return None


def _section_shots(sec: ScriptSection, script: Script, sents, start: float, end: float):
    brief = script.brief
    pid = sec.product_id
    kind = sec.kind
    if kind == "hook":
        if brief.format == "deal_short" and pid:
            return [Shot("deal", sec.id, start, end, pid)]
        return [Shot("title", sec.id, start, end, params={"heading": sec.heading})]
    if kind in ("disclosure", "method", "sample_notice"):
        return [
            Shot(
                "notice", sec.id, start, end, params={"heading": sec.heading, "body": sec.narration}
            )
        ]
    if kind == "intro":
        return [Shot("weights", sec.id, start, end, animate=True)]
    if kind == "outro":
        return [Shot("outro", sec.id, start, end)]
    if kind == "price":
        return [Shot("price_chart", sec.id, start, end, pid or brief.product_ids[0])]
    if kind == "comparison":
        a, b = brief.product_ids[:2] if len(brief.product_ids) >= 2 else (pid, None)
        cat = next((c for s in sents for r in s.refs if (c := _category_of(r))), None)
        if b is None:
            return [Shot("scorecard", sec.id, start, end, a, {"highlight": cat}, animate=True)]
        return [
            Shot(
                "compare", sec.id, start, end, None, {"a": a, "b": b, "category": cat}, animate=True
            )
        ]
    if kind == "verdict":
        winner = brief.verdict_product_id
        if brief.format in ("best_under", "monthly_refresh"):
            mid = start + (end - start) * 0.55
            return [
                Shot("ranking", sec.id, start, mid, params={"winner": winner}, animate=True),
                Shot("product", sec.id, mid, end, winner, {"verdict": True}),
            ]
        if winner is None and len(brief.product_ids) >= 2:
            a, b = brief.product_ids[:2]
            return [
                Shot(
                    "compare",
                    sec.id,
                    start,
                    end,
                    None,
                    {"a": a, "b": b, "category": None},
                    animate=True,
                )
            ]
        return [Shot("product", sec.id, start, end, winner or pid, {"verdict": True})]

    # product sections (and anything else about one product)
    target = pid or brief.product_ids[0]
    last_claim = max((i for i, s in enumerate(sents) if s.refs), default=-1)
    shots: list[Shot] = []
    current: tuple[str, dict[str, Any]] = ("product", {})
    for i, s in enumerate(sents):
        if i > last_claim >= 0:
            visual: tuple[str, dict[str, Any]] = ("buyskip", {})
        else:
            visual = _sentence_visual(s.refs) or current
        current = visual
        shots.append(Shot(visual[0], sec.id, s.start, s.end, target, dict(visual[1])))
    if not shots:
        return [Shot("product", sec.id, start, end, target)]
    shots[0].start = start
    return shots


def plan_shots(script: Script, timeline: Timeline) -> list[Shot]:
    by_section = {s.id: s for s in script.sections}
    sents_by_section: dict[str, list] = {}
    for s in timeline.sentences:
        sents_by_section.setdefault(s.section_id, []).append(s)

    raw: list[Shot] = []
    for span in timeline.sections:
        raw += _section_shots(
            by_section[span.section_id],
            script,
            sents_by_section.get(span.section_id, []),
            span.start,
            span.end,
        )

    # Scorecard highlight changes within a run shouldn't cut; merge by kind+product.
    merged: list[Shot] = []
    for shot in raw:
        if (
            merged
            and merged[-1].kind == shot.kind == "scorecard"
            and (merged[-1].product_id == shot.product_id)
        ):
            merged[-1].end = shot.end
            continue
        if merged and merged[-1].same_visual(shot):
            merged[-1].end = shot.end
            continue
        merged.append(shot)

    # Tile the timeline: each shot runs until the next starts; first from 0, last to the end.
    if merged:
        merged[0].start = 0.0
        for a, b in itertools.pairwise(merged):
            a.end = b.start
        merged[-1].end = timeline.duration

    # Animate the first appearance of each animatable visual per product.
    seen: set[tuple[str, str | None]] = set()
    for s in merged:
        key = (s.kind, s.product_id)
        if s.kind in ("scorecard", "ranking", "weights", "compare") and key not in seen:
            s.animate = True
            seen.add(key)
        elif s.kind == "scorecard":
            s.animate = False
    return merged
