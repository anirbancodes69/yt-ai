"""Generate a script for one brief, fact-check it, and save both.

out/videos/<video_id>/script.json      the script (writer + system sections)
out/videos/<video_id>/factcheck.json   the report; render/upload require status=pass
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from channel.config import Settings
from channel.data import Dataset, load_dataset, make_provider
from channel.factcheck.checker import (
    FactChecker,
    FactCheckReport,
    Issue,
    data_fingerprint,
    load_factcheck_config,
)
from channel.facts import Facts, build_facts, subset_for
from channel.formats import FormatsConfig, load_formats
from channel.scoring import ScoringConfig, load_scoring_config
from channel.scripts import prompts
from channel.scripts.briefs import build_brief
from channel.scripts.llm import CachedLLM, strict_schema
from channel.scripts.models import Brief, Script, WriterScript
from channel.scripts.templates import write_from_template
from channel.scripts.text import assemble, load_script_text, system_sections

Writer = Literal["template", "llm"]


class FactCheckFailed(RuntimeError):
    def __init__(self, report: FactCheckReport, video_dir: Path) -> None:
        super().__init__(report.summary())
        self.report = report
        self.video_dir = video_dir


@dataclass
class Context:
    """Everything loaded once per run."""

    settings: Settings
    dataset: Dataset
    scoring: ScoringConfig
    formats: FormatsConfig
    script_text: dict[str, Any]
    fingerprint: str

    @classmethod
    def load(cls, settings: Settings) -> Context:
        provider = make_provider(settings.price_provider, csv_path=settings.paths.prices)
        return cls(
            settings=settings,
            dataset=load_dataset(settings.paths.products, provider),
            scoring=load_scoring_config(settings.paths.scoring),
            formats=load_formats(settings.paths.formats),
            script_text=load_script_text(settings.paths.script_text),
            fingerprint=data_fingerprint(settings.data_files()),
        )

    def facts(self, as_of: date) -> Facts:
        s = self.settings
        return build_facts(
            self.dataset,
            self.scoring,
            as_of=as_of,
            bands=self.formats.price_bands_inr,
            max_price_age_days=s.max_price_age_days,
            history_days=s.price_history_days,
            lookback_days=self.formats.spec("monthly_refresh").lookback_days,
        )

    def brief(self, fmt: str, params: dict[str, str | int], as_of: date, lang: str) -> Brief:
        return build_brief(
            fmt,
            params,
            dataset=self.dataset,
            cfg=self.scoring,
            formats=self.formats,
            as_of=as_of,
            lang=lang,
            max_price_age_days=self.settings.max_price_age_days,
        )

    def checker(self, brief: Brief) -> FactChecker:
        return FactChecker(
            facts=self.facts(brief.as_of),
            brief=brief,
            dataset=self.dataset,
            config=load_factcheck_config(self.settings.paths.factcheck),
            script_text=self.script_text,
            words_per_minute=self.formats.words_per_minute,
            fingerprint=self.fingerprint,
        )

    def video_dir(self, video_id: str) -> Path:
        return self.settings.paths.out / "videos" / video_id


def build_script(ctx: Context, brief: Brief, writer_script: WriterScript, writer: str) -> Script:
    system = system_sections(brief, sample=ctx.dataset.is_sample, text=ctx.script_text)
    return Script(
        video_id=brief.video_id,
        title=writer_script.title,
        writer=writer,
        sample=ctx.dataset.is_sample,
        data_fingerprint=ctx.fingerprint,
        brief=brief,
        sections=assemble(writer_script.sections, system),
    )


def fact_check(ctx: Context, script: Script) -> FactCheckReport:
    """Check a script against a brief rebuilt from current data (never the script's own)."""
    b = script.brief
    rebuilt = ctx.brief(b.format, b.params, b.as_of, b.lang)
    return ctx.checker(rebuilt).check(script)


def save(ctx: Context, script: Script, report: FactCheckReport) -> Path:
    d = ctx.video_dir(script.video_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
    (d / "factcheck.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return d


def _parse_writer_json(text: str) -> tuple[WriterScript | None, list[Issue]]:
    try:
        return WriterScript.model_validate_json(text), []
    except ValidationError as exc:
        return None, [Issue(code="invalid_json", detail=str(exc)[:2000])]


def generate(
    ctx: Context,
    brief: Brief,
    *,
    writer: Writer,
    llm: CachedLLM | None = None,
) -> tuple[Script, FactCheckReport, Path]:
    """Write → assemble → fact-check (→ re-prompt with errors) → save.

    Raises FactCheckFailed (after saving the failing report) if the final script
    does not pass.
    """
    facts = ctx.facts(brief.as_of)
    if writer == "template":
        ws = write_from_template(brief, facts, ctx.dataset, ctx.scoring)
        script = build_script(ctx, brief, ws, "template")
        report = fact_check(ctx, script)
    else:
        if llm is None:
            raise ValueError("LLM writer needs a CachedLLM")
        schema = strict_schema(WriterScript.model_json_schema())
        names = {
            p: f"{ctx.dataset.product(p).brand} {ctx.dataset.product(p).model}"
            for p in brief.product_ids
        }
        user = prompts.user_message(
            brief, subset_for(facts, brief.product_ids, brief.band_inr), names
        )
        message = user
        attempts = ctx.settings.llm.fix_attempts + 1
        script, report = None, None
        for attempt in range(attempts):
            resp = llm.complete(
                video_id=brief.video_id,
                operation="script" if attempt == 0 else f"script_fix_{attempt}",
                system=prompts.SYSTEM,
                user=message,
                schema=schema,
            )
            ws, parse_errors = _parse_writer_json(resp.text)
            if ws is None:
                errors = parse_errors
            else:
                script = build_script(ctx, brief, ws, f"llm:{resp.model}")
                report = fact_check(ctx, script)
                if report.status == "pass":
                    break
                errors = report.errors
            message = prompts.fix_message(user, resp.text, errors)
        if script is None or report is None:
            raise RuntimeError(f"{brief.video_id}: model never returned a valid script")

    d = save(ctx, script, report)
    if report.status != "pass":
        raise FactCheckFailed(report, d)
    return script, report, d


def recheck(ctx: Context, script_path: Path) -> tuple[FactCheckReport, Path]:
    """Re-run the fact-check on a saved script and overwrite its report."""
    script = Script.model_validate_json(script_path.read_text(encoding="utf-8"))
    report = fact_check(ctx, script)
    (script_path.parent / "factcheck.json").write_text(report.model_dump_json(indent=2), "utf-8")
    return report, script_path.parent


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
