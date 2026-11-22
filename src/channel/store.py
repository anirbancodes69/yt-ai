"""SQLite operational store: API response cache, cost ledger, budget guard.

Product data stays in data/ (reviewable in git); this DB holds derived state.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    month TEXT NOT NULL,
    video_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    units INTEGER DEFAULT 0,
    usd REAL NOT NULL,
    inr REAL NOT NULL,
    cached INTEGER NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ledger_month ON cost_ledger(month);
CREATE INDEX IF NOT EXISTS ledger_video ON cost_ledger(video_id);
"""


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class CostEntry:
    video_id: str
    provider: str
    operation: str
    usd: float
    inr: float
    cached: bool
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    units: int = 0  # e.g. TTS characters
    note: str | None = None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # cache
    def cache_get(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT payload FROM api_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def cache_put(self, key: str, provider: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?, ?)",
            (key, provider, json.dumps(payload, ensure_ascii=False), _now().isoformat()),
        )
        self.db.commit()

    # ledger
    def log_cost(self, e: CostEntry, *, when: datetime | None = None) -> None:
        when = when or _now()
        self.db.execute(
            """INSERT INTO cost_ledger (created_at, month, video_id, provider, operation, model,
               input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, units,
               usd, inr, cached, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                when.isoformat(),
                when.strftime("%Y-%m"),
                e.video_id,
                e.provider,
                e.operation,
                e.model,
                e.input_tokens,
                e.output_tokens,
                e.cache_read_tokens,
                e.cache_write_tokens,
                e.units,
                e.usd,
                e.inr,
                int(e.cached),
                e.note,
            ),
        )
        self.db.commit()

    def month_spend_inr(self, month: str | None = None) -> float:
        month = month or _now().strftime("%Y-%m")
        row = self.db.execute(
            "SELECT COALESCE(SUM(inr), 0) AS s FROM cost_ledger WHERE month = ?", (month,)
        ).fetchone()
        return float(row["s"])

    def video_spend_inr(self, video_id: str) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(inr), 0) AS s FROM cost_ledger WHERE video_id = ?", (video_id,)
        ).fetchone()
        return float(row["s"])

    def month_units(self, provider: str, month: str | None = None) -> int:
        """Billable units (e.g. TTS characters) used this month, excluding cache hits."""
        month = month or _now().strftime("%Y-%m")
        row = self.db.execute(
            "SELECT COALESCE(SUM(units), 0) AS s FROM cost_ledger "
            "WHERE month = ? AND provider = ? AND cached = 0",
            (month, provider),
        ).fetchone()
        return int(row["s"])

    def video_costs(self, video_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM cost_ledger WHERE video_id = ? ORDER BY id", (video_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def month_by_video(self, month: str | None = None) -> list[dict[str, Any]]:
        month = month or _now().strftime("%Y-%m")
        rows = self.db.execute(
            """SELECT video_id, COUNT(*) AS calls, SUM(cached) AS cached_calls,
                      ROUND(SUM(usd), 4) AS usd, ROUND(SUM(inr), 2) AS inr
               FROM cost_ledger WHERE month = ? GROUP BY video_id ORDER BY video_id""",
            (month,),
        ).fetchall()
        return [dict(r) for r in rows]


@dataclass(frozen=True)
class Budget:
    monthly_inr: float
    per_video_inr: float

    def check(self, store: Store, video_id: str) -> None:
        """Refuse a new paid call if this month or this video is already at its cap."""
        month = store.month_spend_inr()
        if month >= self.monthly_inr:
            raise BudgetExceeded(f"monthly budget reached: ₹{month:.2f} of ₹{self.monthly_inr:.0f}")
        video = store.video_spend_inr(video_id)
        if video >= self.per_video_inr:
            raise BudgetExceeded(
                f"{video_id}: per-video budget reached: ₹{video:.2f} of ₹{self.per_video_inr:.0f}"
            )


def _now() -> datetime:
    return datetime.now(UTC)
