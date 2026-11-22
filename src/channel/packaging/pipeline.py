"""Package a rendered video, or preview packaging for any brief.

out/videos/<video_id>/package/
    package.json         title, variants, description, tags, chapters, links, flags
    description.txt      the description exactly as it will be uploaded
    thumbnail.png        1280x720 (1080x1920 cover for Shorts)
    package_check.json   pass/fail; upload requires pass for THIS package and script
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from channel.factcheck import FactCheckBlocked, require_passed
from channel.factcheck.checker import load_factcheck_config
from channel.packaging.builder import PackagingError, build_package
from channel.packaging.checks import PackageCheckReport, check_package, package_sha256
from channel.packaging.models import Package, PackagingConfig, load_packaging
from channel.packaging.thumbnail import render_thumbnail
from channel.render.pipeline import load_render_text, render_data_for
from channel.scripts.models import Brief, Script
from channel.scripts.pipeline import Context
from channel.scripts.text import system_sections
from channel.voice.narration import SectionSpan


class PackageBlocked(RuntimeError):
    pass


def _spans(video_dir: Path) -> list[SectionSpan]:
    tl = video_dir / "timeline.json"
    if not tl.exists():
        return []
    data = json.loads(tl.read_text(encoding="utf-8"))
    return [SectionSpan(**s) for s in data["sections"]]


def _method_note(ctx: Context, brief: Brief) -> str:
    """The same method sentence the narration speaks, so the description repeats it exactly."""
    sections = system_sections(brief, sample=ctx.dataset.is_sample, text=ctx.script_text)
    return next(s.narration for s in sections if s.kind == "method")


def _build(
    ctx: Context,
    cfg: PackagingConfig,
    brief: Brief,
    out_dir: Path,
    *,
    env: Mapping[str, str],
    script: Script | None,
    spans: list[SectionSpan] | None,
    variant: int,
    video_id: str | None,
    script_sha: str = "",
) -> tuple[Package, PackageCheckReport]:
    s = ctx.settings
    months = ctx.script_text[brief.lang]["months"]
    facts = ctx.facts(brief.as_of)
    pkg = build_package(
        brief=brief,
        facts=facts,
        dataset=ctx.dataset,
        cfg=cfg,
        months=months,
        method_note=_method_note(ctx, brief),
        env=env,
        max_price_age_days=s.max_price_age_days,
        video_id=video_id,
        script=script,
        spans=spans,
        variant=variant,
    )
    pkg = pkg.model_copy(update={"script_sha256": script_sha, "data_fingerprint": ctx.fingerprint})

    out_dir.mkdir(parents=True, exist_ok=True)
    rd = render_data_for(ctx, brief)
    render_thumbnail(rd, cfg.lang(brief.lang), out_dir / pkg.thumbnail, months=months)
    (out_dir / "description.txt").write_text(pkg.description, encoding="utf-8")
    (out_dir / "package.json").write_text(pkg.model_dump_json(indent=2), encoding="utf-8")

    report = check_package(
        pkg,
        cfg=cfg,
        facts=facts,
        dataset=ctx.dataset,
        factcheck_cfg=load_factcheck_config(s.paths.factcheck),
        months=months,
        as_of=brief.as_of,
        package_dir=out_dir,
        product_ids=brief.product_ids,
    )
    (out_dir / "package_check.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return pkg, report


def package_video(
    ctx: Context, video_dir: Path, *, variant: int = 0, env: Mapping[str, str] | None = None
) -> tuple[Package, PackageCheckReport, Path]:
    """Package a fact-checked (and, for long-form, rendered) video."""
    from channel.factcheck.checker import sha256_text

    script = require_passed(video_dir, ctx.settings.data_files())  # never package unchecked text
    cfg = load_packaging(ctx.settings.paths.packaging)
    pkg_dir = video_dir / "package"
    pkg, report = _build(
        ctx,
        cfg,
        script.brief,
        pkg_dir,
        env=os.environ if env is None else env,
        script=script,
        spans=_spans(video_dir),
        variant=variant,
        video_id=script.video_id,
        script_sha=sha256_text(script.canonical_json()),
    )
    return pkg, report, pkg_dir


def preview_package(
    ctx: Context,
    brief: Brief,
    out_dir: Path,
    *,
    variant: int = 0,
    env: Mapping[str, str] | None = None,
) -> tuple[Package, PackageCheckReport]:
    """Packaging from a brief alone (no script, no render): for reviewing every format.
    Marked ``preview`` and never accepted by the upload gate."""
    cfg = load_packaging(ctx.settings.paths.packaging)
    try:
        return _build(
            ctx,
            cfg,
            brief,
            out_dir,
            env=os.environ if env is None else env,
            script=None,
            spans=None,
            variant=variant,
            video_id=brief.video_id,
        )
    except PackagingError:
        raise


def require_package(video_dir: Path, data_files: tuple[Path, ...]) -> Package:
    """Upload gate: the script passed its fact-check AND the package passed its check, and
    neither changed since."""
    from channel.factcheck.checker import sha256_text

    try:
        script = require_passed(video_dir, data_files)
    except FactCheckBlocked as exc:
        raise PackageBlocked(str(exc)) from exc
    pkg_dir = video_dir / "package"
    for name in ("package.json", "package_check.json"):
        if not (pkg_dir / name).exists():
            raise PackageBlocked(f"{video_dir.name}: no {name}; run `channel package`")
    pkg = Package.model_validate_json((pkg_dir / "package.json").read_text(encoding="utf-8"))
    report = PackageCheckReport.model_validate_json(
        (pkg_dir / "package_check.json").read_text(encoding="utf-8")
    )
    if pkg.preview:
        raise PackageBlocked(f"{video_dir.name}: package is a preview, not built from a script")
    if report.status != "pass":
        raise PackageBlocked(
            f"{video_dir.name}: package check FAILED ({len(report.errors)} errors)"
        )
    if pkg.script_sha256 != sha256_text(script.canonical_json()):
        raise PackageBlocked(f"{video_dir.name}: script changed after packaging; re-run it")
    if report.package_sha256 != package_sha256(pkg, pkg_dir / pkg.thumbnail):
        raise PackageBlocked(f"{video_dir.name}: package files changed after the check")
    if pkg.orientation == "landscape" and not (video_dir / "video.mp4").exists():
        raise PackageBlocked(f"{video_dir.name}: video.mp4 not rendered yet")
    return pkg


def preview_all(
    ctx: Context, out_dir: Path, *, lang: str, as_of: date
) -> list[tuple[Package, PackageCheckReport]]:
    """One preview package per format (best_under/launch/monthly/h2h/deal)."""
    from channel.render.pipeline import CONTACT_PARAMS

    results = []
    for fmt, params in CONTACT_PARAMS.items():
        brief = ctx.brief(fmt, params, as_of, lang)
        results.append(preview_package(ctx, brief, out_dir / fmt))
    return results


__all__ = [
    "PackageBlocked",
    "load_render_text",
    "package_video",
    "preview_all",
    "preview_package",
    "require_package",
]
