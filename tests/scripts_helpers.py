"""Shared helpers for Phase 2 tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from channel.scripts.models import Script


def mutate(script: Script, fn: Callable[[dict[str, Any]], None]) -> Script:
    data = json.loads(script.model_dump_json())
    fn(data)
    return Script.model_validate(data)


def section(data: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(s for s in data["sections"] if s["id"] == sid)


def claim(sec: dict[str, Any], ref_suffix: str) -> dict[str, Any]:
    return next(c for c in sec["claims"] if c["ref"].endswith(ref_suffix))


def codes(report) -> list[str]:
    return [e.code for e in report.errors]
