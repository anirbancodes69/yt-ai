"""Render one fact-checked script to MP4.

    gate → narration (TTS) → shot plan → frames → captions → ffmpeg → probe

out/videos/<video_id>/
    video.mp4, narration.wav, captions.ass, captions.srt, timeline.json,
    render.json (duration, size, TTS provider), cost.json (all API costs for this video)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from channel.data.pricing import price_history
from channel.factcheck import require_passed
from channel.render.assemble import (
    FrameList,
    RenderError,
    ffmpeg_command,
    pick_music,
    probe,
    run_ffmpeg,
)
from channel.render.captions import write_ass, write_srt
from channel.render.fonts import font_set
from channel.render.frames import (
    Canvas,
    RenderData,
    card_buyskip,
    card_compare,
    card_deal,
    card_notice,
    card_outro,
    card_price_chart,
    card_product,
    card_ranking,
    card_scorecard,
    card_title,
    card_weights,
)
from channel.render.shots import Shot, plan_shots
from channel.render.theme import theme_for
from channel.scripts.models import Script
from channel.scripts.pipeline import Context
from channel.store import Budget, Store
from channel.voice import CachedTTS, TTSProvider, build_narration, make_provider
from channel.voice.narration import fit_duration


@dataclass
class RenderResult:
    video: Path
    duration: float
    width: int
    height: int
    shots: int
    frames: int
    cost_inr: float


def load_render_text(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def render_data(ctx: Context, script: Script) -> RenderData:
    brief = script.brief
    s = ctx.settings
    text = ctx.script_text
    rtext = load_render_text(s.paths.render_text)
    return RenderData(
        brief=brief,
        facts=ctx.facts(brief.as_of),
        dataset=ctx.dataset,
        scoring=ctx.scoring,
        theme=theme_for(brief.format),
        fonts=font_set(s.paths.fonts),
        text=rtext["render"][brief.lang],
        press_dir=s.paths.press,
        history={
            pid: [
                h
                for h in price_history(ctx.dataset.prices, pid, as_of=brief.as_of)
                if (brief.as_of - h.on).days <= s.price_history_days
            ]
            for pid in brief.product_ids
        },
        history_days=s.price_history_days,
        months=text[brief.lang]["months"],
        scale=s.render.scale,
    )


def draw_shot(rd: RenderData, shot: Shot, progress: float = 1.0) -> Image.Image:
    c = Canvas(rd, portrait=rd.brief.orientation == "portrait")
    p, pid = shot.params, shot.product_id
    if shot.kind == "title":
        card_title(c, p.get("heading") or "")
    elif shot.kind == "notice":
        card_notice(c, p["heading"], p["body"])
    elif shot.kind == "weights":
        card_weights(c, progress)
    elif shot.kind == "product":
        card_product(c, pid, verdict=bool(p.get("verdict")))
    elif shot.kind == "scorecard":
        card_scorecard(c, pid, progress, p.get("highlight"))
    elif shot.kind == "price_chart":
        card_price_chart(c, pid)
    elif shot.kind == "buyskip":
        card_buyskip(c, pid)
    elif shot.kind == "ranking":
        card_ranking(c, rd.brief.product_ids, progress, p.get("winner"))
    elif shot.kind == "compare":
        card_compare(c, p["a"], p["b"], progress, p.get("category"))
    elif shot.kind == "deal":
        card_deal(c, pid)
    elif shot.kind == "outro":
        card_outro(c)
    else:
        raise RenderError(f"unknown shot kind {shot.kind!r}")
    return c.image()


def render_frames(
    rd: RenderData,
    shots: list[Shot],
    frames_dir: Path,
    *,
    fps: int,
    anim_frames: int,
    crossfade: int,
) -> FrameList:
    frames_dir.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, float]] = []
    step = 1 / fps
    prev_last: Image.Image | None = None
    n = 0

    def save(img: Image.Image, dur: float) -> None:
        nonlocal n
        name = f"f{n:06d}.png"
        img.save(frames_dir / name, compress_level=1)
        entries.append((f"frames/{name}", dur))
        n += 1

    for shot in shots:
        total = max(step, shot.duration)
        used = 0.0
        final = draw_shot(rd, shot, 1.0)
        anim = anim_frames if shot.animate else 0
        fade = crossfade if prev_last is not None else 0
        budget_frames = int(total / step) - 1
        anim = max(0, min(anim, budget_frames))
        fade = max(0, min(fade, budget_frames - anim))
        for i in range(max(anim, fade)):
            frame = draw_shot(rd, shot, (i + 1) / anim) if i < anim else final
            if i < fade and prev_last is not None:
                frame = Image.blend(prev_last, frame, (i + 1) / (fade + 1))
            save(frame, step)
            used += step
        save(final, total - used)
        prev_last = final
    return FrameList(entries)


def render_video(
    ctx: Context,
    video_dir: Path,
    *,
    tts_provider: str,
    keep_frames: bool = False,
    provider: TTSProvider | None = None,
) -> RenderResult:
    s = ctx.settings
    script = require_passed(video_dir, s.data_files())  # never render an unchecked script
    brief = script.brief
    portrait = brief.orientation == "portrait"

    store = Store(s.paths.db)
    try:
        provider = provider or make_provider(tts_provider, brief.lang, s.tts)
        tts = CachedTTS(
            provider,
            cache_dir=s.paths.cache / "tts",
            store=store,
            budget=Budget(s.budget.monthly_inr, s.budget.per_video_inr),
            price=s.tts.pricing[tts_provider],
            usd_to_inr=s.budget.usd_to_inr,
        )
        r = s.render
        timeline = build_narration(
            script,
            tts,
            video_dir / "narration.wav",
            pronounce=load_render_text(s.paths.render_text)
            .get("pronounce", {})
            .get(brief.lang, {}),
            sentence_gap=r.sentence_gap_s,
            section_gap=r.section_gap_s,
            lead_in=r.lead_in_s,
            tail=r.tail_s,
        )
        spec = ctx.formats.spec(brief.format)
        tempo = fit_duration(
            video_dir / "narration.wav",
            timeline,
            target=spec.target_seconds,
            max_speedup=r.max_speedup,
            max_slowdown=r.max_slowdown,
        )
        (video_dir / "timeline.json").write_text(
            json.dumps(timeline.to_json(), ensure_ascii=False, indent=1), "utf-8"
        )

        rd = render_data(ctx, script)
        shots = plan_shots(script, timeline)
        build = video_dir / "build"
        if build.exists():
            shutil.rmtree(build)
        frames = render_frames(
            rd,
            shots,
            build / "frames",
            fps=r.fps,
            anim_frames=r.anim_frames,
            crossfade=r.crossfade_frames,
        )
        frames.write(build / "frames.txt")

        probe_canvas = Canvas(rd, portrait)
        width, height = probe_canvas.W, probe_canvas.H
        (video_dir / "captions.ass").write_text(
            write_ass(timeline, rd.theme, width=width, height=height, portrait=portrait), "utf-8"
        )
        (video_dir / "captions.srt").write_text(write_srt(timeline), "utf-8")
        shutil.copy(video_dir / "captions.ass", build / "captions.ass")

        music = pick_music(s.paths.music, script.video_id)
        cmd = ffmpeg_command(
            frames_list="frames.txt",
            narration=str((video_dir / "narration.wav").resolve()),
            captions="captions.ass",
            output=str((video_dir / "video.mp4").resolve()),
            duration=timeline.duration,
            fps=r.fps,
            preset=r.x264_preset,
            crf=r.crf,
            music=music.resolve() if music else None,
            music_db=r.music_volume_db,
        )
        run_ffmpeg(cmd, cwd=build)
        info = probe(video_dir / "video.mp4")

        costs = store.video_costs(script.video_id)
        cost_inr = round(sum(c["inr"] for c in costs), 2)
        summary = {
            "video_id": script.video_id,
            "lines": {
                "llm_inr": round(sum(c["inr"] for c in costs if c["provider"] == "anthropic"), 2),
                "tts_inr": round(sum(c["inr"] for c in costs if c["operation"] == "tts"), 2),
                "tts_chars_synthesized": sum(c["units"] for c in costs if c["operation"] == "tts"),
                "cache_hits": sum(1 for c in costs if c["cached"]),
                "api_calls": sum(1 for c in costs if not c["cached"]),
            },
            "total_inr": cost_inr,
        }
        (video_dir / "cost.json").write_text(json.dumps(summary, indent=2), "utf-8")
        (video_dir / "render.json").write_text(
            json.dumps(
                {
                    **info,
                    "tts_provider": tts_provider,
                    "narration_tempo": round(tempo, 4),
                    "music": music.name if music else None,
                    "shots": len(shots),
                    "frames": len(frames.entries),
                    "orientation": brief.orientation,
                    "shot_plan": [sh.to_json() for sh in shots],
                },
                indent=1,
                ensure_ascii=False,
            ),
            "utf-8",
        )
        if not keep_frames:
            shutil.rmtree(build)
        return RenderResult(
            video_dir / "video.mp4",
            info["duration"],
            info["width"],
            info["height"],
            len(shots),
            len(frames.entries),
            cost_inr,
        )
    finally:
        store.close()


CONTACT_SHOTS = {
    "best_under": ("product", "scorecard", "ranking"),
    "head_to_head": ("title", "compare", "product"),
    "launch_explainer": ("product", "scorecard", "price_chart"),
    "deal_short": ("deal", "price_chart", "buyskip"),
    "monthly_refresh": ("ranking", "product", "buyskip"),
}
CONTACT_PARAMS = {
    "best_under": {"band": 20000},
    "head_to_head": {"a": "tarang-t9", "b": "ostra-o2"},
    "launch_explainer": {"product": "zentra-z8-pro"},
    "deal_short": {"product": "ostra-o2"},
    "monthly_refresh": {"band": 30000},
}


def contact_sheet(ctx: Context, out: Path, *, lang: str, as_of) -> Path:
    """One row per format with three representative frames, rendered from data
    (no script needed) — shows the five formats are visually distinct."""
    thumbs: list[list[Image.Image]] = []
    for fmt, kinds in CONTACT_SHOTS.items():
        brief = ctx.brief(fmt, CONTACT_PARAMS[fmt], as_of, lang)
        fake = Script(
            video_id=brief.video_id,
            title="",
            writer="contact-sheet",
            sample=ctx.dataset.is_sample,
            data_fingerprint="",
            brief=brief,
            sections=[],
        )
        rd = render_data(ctx, fake)
        rd.scale = 0.25
        pid = brief.product_ids[0]
        row = []
        for kind in kinds:
            params: dict = {}
            if kind == "title":
                params = {
                    "heading": f"{rd.name(brief.product_ids[0])} vs {rd.name(brief.product_ids[1])}"
                }
            elif kind == "ranking":
                params = {"winner": brief.verdict_product_id}
            elif kind == "compare":
                params = {
                    "a": brief.product_ids[0],
                    "b": brief.product_ids[1],
                    "category": "display",
                }
            elif kind == "product" and fmt == "head_to_head":
                params = {"verdict": True}
            row.append(draw_shot(rd, Shot(kind, "x", 0, 1, pid, params)))
        thumbs.append(row)
    cell_w, cell_h, pad = 480, 480, 12
    sheet = Image.new(
        "RGB", (3 * (cell_w + pad) + pad, len(thumbs) * (cell_h + pad) + pad), (12, 12, 12)
    )
    for r, row in enumerate(thumbs):
        for c, im in enumerate(row):
            im = im.copy()
            im.thumbnail((cell_w, cell_h))
            x = pad + c * (cell_w + pad) + (cell_w - im.width) // 2
            y = pad + r * (cell_h + pad) + (cell_h - im.height) // 2
            sheet.paste(im, (x, y))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out
