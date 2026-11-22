"""Fact-checker: verifies every claim and every number in a script against the data.

Rules (any violation is an error, and errors block render and upload):

1. Every claim's ``ref`` exists in the fact table rebuilt from the data files.
2. The claimed ``value`` equals the fact.
3. The claim ``text`` appears verbatim in the section narration, and the
   numbers inside it equal the fact (exactly one number for numeric facts;
   true/false claims must agree with negation words).
4. After removing claim spans and allowed tokens (product and chipset names,
   "5G"…), the narration contains no other numbers. Headings and the title may
   only contain numbers that were verified in claims.
5. No hands-on/testing phrases (config/factcheck.yaml).
6. The disclosure, method note and SAMPLE notice are present and unchanged.
7. Structure: hook first, a verdict that names the expected product, no product
   talk after the verdict, every ranked product covered (list formats), refs only
   to products in the brief.
8. The brief, SAMPLE flag and data fingerprint match the current data.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from channel.data.models import Dataset
from channel.facts import Facts, FactValue, product_id_of
from channel.scripts.models import Brief, Script, ScriptSection
from channel.scripts.text import system_sections

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUM_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MASK = " ⁣ "  # invisible separator: keeps words apart, contains no digits


@dataclass(frozen=True)
class FactCheckConfig:
    banned_phrases: dict[str, list[str]]
    allowed_tokens: list[str]
    negation_words: dict[str, list[str]]
    value_aliases: dict[str, list[str]]
    multipliers: dict[str, int]


def load_factcheck_config(path: Path) -> FactCheckConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return FactCheckConfig(**raw)


class Issue(BaseModel):
    code: str
    section_id: str | None = None
    ref: str | None = None
    detail: str


class FactCheckReport(BaseModel):
    video_id: str
    status: Literal["pass", "fail"]
    checked_at: datetime
    script_sha256: str
    data_fingerprint: str
    claims_checked: int
    estimated_seconds: float
    errors: list[Issue]
    warnings: list[Issue]

    def summary(self) -> str:
        head = (
            f"{self.status.upper()}: {self.video_id} — {self.claims_checked} claims, "
            f"{len(self.errors)} errors, {len(self.warnings)} warnings, "
            f"~{self.estimated_seconds / 60:.1f} min"
        )
        lines = [head]
        for i in self.errors:
            where = f"[{i.section_id}] " if i.section_id else ""
            lines.append(f"  ERROR {i.code}: {where}{i.detail}")
        for i in self.warnings:
            lines.append(f"  warn  {i.code}: {i.detail}")
        return "\n".join(lines)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def data_fingerprint(files: tuple[Path, ...]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ----------------------------------------------------------------------------- numbers


def extract_numbers(text: str, multipliers: dict[str, int]) -> list[float]:
    """All numbers in ``text``: Indian/Western grouping, decimals, Devanagari
    digits, and multiplier words ("7.2 लाख" -> 720000)."""
    text = text.translate(DEVANAGARI_DIGITS)
    out: list[float] = []
    for m in NUM_RE.finditer(text):
        v = float(m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else ""))
        tail = text[m.end() : m.end() + 16].lstrip().casefold()
        for word, mult in multipliers.items():
            if tail.startswith(word.casefold()):
                v *= mult
                break
        out.append(v)
    return out


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_equal(claimed: object, fact: FactValue) -> bool:
    if isinstance(fact, bool) or isinstance(claimed, bool):
        return isinstance(claimed, bool) and isinstance(fact, bool) and claimed == fact
    if _is_number(fact):
        if isinstance(claimed, str):
            try:
                claimed = float(claimed.replace(",", ""))
            except ValueError:
                return False
        return _is_number(claimed) and math.isclose(float(claimed), float(fact), abs_tol=1e-9)
    return isinstance(claimed, str) and claimed.strip().casefold() == str(fact).strip().casefold()


def _close_in(x: float, pool: list[float]) -> bool:
    return any(math.isclose(x, p, abs_tol=1e-9) for p in pool)


# ----------------------------------------------------------------------------- checker


class FactChecker:
    def __init__(
        self,
        *,
        facts: Facts,
        brief: Brief,
        dataset: Dataset,
        config: FactCheckConfig,
        script_text: dict[str, Any],
        words_per_minute: dict[str, int],
        fingerprint: str,
    ) -> None:
        self.facts = facts
        self.brief = brief
        self.dataset = dataset
        self.cfg = config
        self.script_text = script_text
        self.wpm = words_per_minute
        self.fingerprint = fingerprint
        tokens = set(config.allowed_tokens)
        for p in dataset.products.values():
            tokens.update({f"{p.brand} {p.model}", p.model})
            chipset = p.specs.get("chipset")
            if chipset is not None:
                tokens.add(str(chipset.value))
        # Longest first so "Z8 Pro" is masked before "Z8"; only tokens with digits matter.
        self._tokens = sorted((t for t in tokens if re.search(r"\d", t)), key=len, reverse=True)
        self._token_re = (
            re.compile("|".join(re.escape(t) for t in self._tokens), re.IGNORECASE)
            if self._tokens
            else None
        )

    # -- helpers
    def _mask(self, text: str) -> str:
        text = text.translate(DEVANAGARI_DIGITS)
        return self._token_re.sub(MASK, text) if self._token_re else text

    def _nums(self, text: str) -> list[float]:
        return extract_numbers(text, self.cfg.multipliers)

    def _negated(self, text: str) -> bool:
        t = f" {text.casefold()} "
        return any(w.casefold() in t for w in self.cfg.negation_words.get(self.brief.lang, []))

    # -- main entry
    def check(self, script: Script) -> FactCheckReport:
        errors: list[Issue] = []
        warnings: list[Issue] = []
        lang = self.brief.lang

        def err(code: str, detail: str, section: str | None = None, ref: str | None = None):
            errors.append(Issue(code=code, section_id=section, ref=ref, detail=detail))

        if script.brief != self.brief:
            err("brief_mismatch", "script brief differs from the brief rebuilt from current data")
        if script.sample != self.dataset.is_sample:
            err(
                "sample_flag",
                f"script.sample={script.sample} but data sample={self.dataset.is_sample}",
            )
        if script.data_fingerprint != self.fingerprint:
            err("stale_script", "data/config changed since this script was generated; regenerate")

        ids = [s.id for s in script.sections]
        if len(ids) != len(set(ids)):
            err("structure", "duplicate section ids")

        self._check_system(script, err)

        writer = [s for s in script.sections if s.source == "writer"]
        claims_checked = 0
        all_verified: list[float] = []
        banned = [b.casefold() for b in self.cfg.banned_phrases.get(lang, [])]

        for text, where in [(script.title, "title")] + [
            (f"{s.heading}\n{s.narration}", s.id) for s in writer
        ]:
            low = text.casefold()
            for phrase in banned:
                if phrase in low:
                    err("banned_phrase", f"implies hands-on testing: {phrase!r}", where)

        for s in writer:
            verified, n = self._check_section(s, err)
            claims_checked += n
            all_verified.extend(verified)
            for x in self._nums(self._mask(s.heading)):
                if not _close_in(x, verified):
                    err("unbacked_number", f"heading number {x:g} not backed by a claim", s.id)

        for x in self._nums(self._mask(script.title)):
            if not _close_in(x, all_verified):
                err("unbacked_number", f"title number {x:g} not backed by any claim", "title")

        self._check_structure(writer, err)

        words = script.narration_words
        seconds = words / self.wpm.get(lang, 140) * 60
        lo, hi = self.brief.target_words
        if not lo <= words <= hi:
            warnings.append(
                Issue(code="length", detail=f"{words} words, target {lo}-{hi} ({seconds:.0f}s)")
            )

        return FactCheckReport(
            video_id=script.video_id,
            status="fail" if errors else "pass",
            checked_at=datetime.now(UTC),
            script_sha256=sha256_text(script.canonical_json()),
            data_fingerprint=self.fingerprint,
            claims_checked=claims_checked,
            estimated_seconds=round(seconds, 1),
            errors=errors,
            warnings=warnings,
        )

    def _check_system(self, script: Script, err) -> None:
        expected = system_sections(self.brief, sample=self.dataset.is_sample, text=self.script_text)
        actual = {s.id: s for s in script.sections if s.source == "system"}
        for exp in expected:
            got = actual.pop(exp.id, None)
            if got is None:
                err("missing_system_section", f"required section {exp.kind!r} is missing", exp.id)
            elif (got.kind, got.heading, got.narration, got.claims) != (
                exp.kind,
                exp.heading,
                exp.narration,
                exp.claims,
            ):
                err("system_section_modified", f"{exp.kind!r} text differs from config", exp.id)
        for extra in actual:
            err("unexpected_system_section", "system section not produced by config", extra)
        first_sys = next((i for i, s in enumerate(script.sections) if s.source == "system"), None)
        if first_sys is not None and first_sys > 1:
            err("structure", "disclosure must come right after the hook")

    def _check_section(self, s: ScriptSection, err) -> tuple[list[float], int]:
        remaining = self._mask(s.narration)
        verified: list[float] = []
        for c in s.claims:
            fact = self.facts.get(c.ref)
            if fact is None:
                err("unknown_ref", f"no such fact {c.ref!r}", s.id, c.ref)
                continue
            pid = product_id_of(c.ref)
            if pid is not None and pid not in self.brief.product_ids:
                err("off_brief_product", f"{pid} is not part of this video", s.id, c.ref)
            if not values_equal(c.value, fact.value):
                err(
                    "value_mismatch",
                    f"claimed {c.value!r} but data says {fact.value!r}",
                    s.id,
                    c.ref,
                )
            text = self._mask(c.text)
            if not text.strip():
                err("claim_text_not_found", "empty claim text", s.id, c.ref)
                continue
            idx = remaining.find(text)
            if idx < 0:
                err("claim_text_not_found", f"{c.text!r} not found in narration", s.id, c.ref)
                continue
            problem = self._text_vs_fact(c.text, text, fact.value)
            if problem:
                err("claim_number_mismatch", f"{c.text!r}: {problem}", s.id, c.ref)
            elif _is_number(fact.value):
                verified.append(float(fact.value))
            remaining = remaining[:idx] + MASK + remaining[idx + len(text) :]
        for x in self._nums(remaining):
            err("unbacked_number", f"number {x:g} in narration has no claim", s.id)
        return verified, len(s.claims)

    def _text_vs_fact(self, raw: str, masked: str, value: FactValue) -> str | None:
        nums = self._nums(masked)
        if isinstance(value, bool):
            if nums:
                return "true/false claim must not contain numbers"
            if value and self._negated(raw):
                return "text negates a true fact"
            if not value and not self._negated(raw):
                return "fact is false but text does not say so"
            return None
        if _is_number(value):
            if not nums and float(value) == 0:
                return None if self._negated(raw) else "zero value must be stated as absence"
            if len(nums) != 1:
                return f"expected exactly one number, found {len(nums)}"
            if not math.isclose(nums[0], float(value), abs_tol=1e-9):
                return f"text says {nums[0]:g}, data says {value}"
            return None
        sval = str(value)
        if ISO_DATE_RE.match(sval):
            parts = [float(p) for p in sval.split("-")]
            if not nums or parts[0] not in nums or any(not _close_in(n, parts) for n in nums):
                return f"date text does not match {sval}"
            return None
        allowed = self._nums(sval)
        if any(not _close_in(n, allowed) for n in nums):
            return f"numbers in text not part of value {sval!r}"
        low = raw.casefold()
        aliases = [a.casefold() for a in self.cfg.value_aliases.get(sval.casefold(), [])]
        if sval.casefold() not in low and not any(a in f" {low} " for a in aliases):
            return f"text does not state {sval!r}"
        return None

    def _check_structure(self, writer: list[ScriptSection], err) -> None:
        if not writer:
            err("structure", "script has no writer sections")
            return
        if writer[0].kind != "hook":
            err("structure", "first section must be the hook", writer[0].id)
        verdict_idx = next((i for i, s in enumerate(writer) if s.kind == "verdict"), None)
        if verdict_idx is None:
            err("structure", "script must end with a verdict section")
            return
        for s in writer[verdict_idx + 1 :]:
            if s.kind not in ("verdict", "outro"):
                err("structure", f"{s.kind!r} section after the verdict", s.id)
        winner = self.brief.verdict_product_id
        if winner is not None:
            verdict = writer[verdict_idx]
            if not any(product_id_of(c.ref) == winner for c in verdict.claims):
                err(
                    "verdict_missing_winner", f"verdict must cite a fact about {winner}", verdict.id
                )
        for s in writer:
            if s.product_id is not None and s.product_id not in self.brief.product_ids:
                err("off_brief_product", f"section about {s.product_id}, not in brief", s.id)
        if self.brief.format in ("best_under", "monthly_refresh"):
            covered = {s.product_id for s in writer if s.kind == "product"}
            for pid in self.brief.product_ids:
                if pid not in covered:
                    err("structure", f"ranked product {pid} has no product section")
