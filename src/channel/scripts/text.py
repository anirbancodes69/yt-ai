"""Code-inserted narration: affiliate disclosure, method note, SAMPLE notice.

Writers never produce these. The fact-checker regenerates them and requires an
exact match, so the disclosure can't be dropped or softened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from channel.fmt import long_date
from channel.scripts.models import Brief, ScriptSection


def load_script_text(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def system_sections(brief: Brief, *, sample: bool, text: dict[str, Any]) -> list[ScriptSection]:
    t = text[brief.lang]
    variant = t["short" if brief.orientation == "portrait" else "long"]
    date_str = long_date(brief.as_of, t["months"])
    kinds = ["disclosure", "method"] + (["sample_notice"] if sample else [])
    return [
        ScriptSection(
            id=f"sys-{kind}",
            kind=kind,  # type: ignore[arg-type]
            source="system",
            heading=t["headings"][kind],
            narration=variant[kind].format(date=date_str),
        )
        for kind in kinds
    ]


def assemble(writer_sections, system: list[ScriptSection]) -> list[ScriptSection]:
    """Hook first, then the system sections, then the rest of the writer's sections."""
    converted = [ScriptSection(source="writer", **s.model_dump()) for s in writer_sections]
    if converted and converted[0].kind == "hook":
        return [converted[0], *system, *converted[1:]]
    return [*system, *converted]
