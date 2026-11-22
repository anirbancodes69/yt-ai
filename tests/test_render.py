from __future__ import annotations

import itertools
import json
import subprocess
from pathlib import Path

import pytest
from PIL import features

from channel.factcheck import FactCheckBlocked
from channel.render.assemble import ffmpeg_command, pick_music, probe
from channel.render.captions import caption_events, chunk_words, write_ass, write_srt
from channel.render.fonts import font_set, script_runs
from channel.render.pipeline import CONTACT_PARAMS, draw_shot, render_data, render_video
from channel.render.shots import Shot, plan_shots
from channel.render.testframe import render_caption_test, render_test_frame
from channel.render.theme import THEMES
from channel.scripts.models import Script
from channel.scripts.pipeline import Context, generate
from channel.voice.narration import Sentence, Timeline
from tests.fakes import ToneTTS

KINDS = (
    "title",
    "notice",
    "weights",
    "product",
    "scorecard",
    "price_chart",
    "buyskip",
    "ranking",
    "compare",
    "deal",
    "outro",
)


# ---------------------------------------------------------------- Devanagari shaping proof


def test_raqm_available() -> None:
    assert features.check("raqm")


def test_script_runs_split_latin_and_devanagari() -> None:
    assert script_runs("Tarang T9 की कीमत ₹18,999 है · OK") == [
        ("latin", "Tarang T9 "),
        ("deva", "की कीमत ₹18,999 है "),
        ("latin", "· OK"),
    ]


def test_pillow_raqm_shapes_devanagari(settings, tmp_path) -> None:
    out = tmp_path / "devanagari.png"
    widths = render_test_frame(font_set(settings.paths.fonts), out)
    assert out.exists()
    # Conjuncts and reordered matras change the advance width vs. unshaped BASIC layout.
    assert all(a != b for a, b in zip(widths["raqm"], widths["basic"], strict=True))


def test_libass_complex_shaping(tmp_path) -> None:
    out = tmp_path / "captions.png"
    pixels = render_caption_test(out)
    assert out.exists() and pixels["simple"] != pixels["complex"]


# ---------------------------------------------------------------- captions


def _timeline() -> Timeline:
    return Timeline(
        sentences=[
            Sentence(
                "s1", 0, "इसकी कीमत 18,999 रुपये है और यह 5,000mAh बैटरी के साथ आता है।", "", 1.0, 5.0
            ),
            Sentence("s2", 0, "फ़ैसला साफ़ है।", "", 5.5, 6.5),
        ],
        sections=[],
        duration=7.0,
    )


def test_chunk_words() -> None:
    assert chunk_words("a bb ccc dddd", 6) == ["a bb", "ccc", "dddd"]


def test_caption_events_cover_sentences() -> None:
    ev = caption_events(_timeline(), 20)
    assert ev[0][0] == pytest.approx(1.0)
    assert max(e[1] for e in ev if e[0] < 5) == pytest.approx(5.0)
    assert " ".join(e[2] for e in ev if e[0] < 5) == _timeline().sentences[0].text


def test_ass_and_srt() -> None:
    ass = write_ass(_timeline(), THEMES["best_under"], width=1920, height=1080, portrait=False)
    assert "PlayResX: 1920" in ass and "Noto Sans Devanagari" in ass
    assert "\\kf" in ass and "\\fad(" in ass
    assert "Dialogue: 0,0:00:01.00," in ass
    srt = write_srt(_timeline())
    assert srt.startswith("1\n00:00:01,000 --> ")
    assert "फ़ैसला साफ़ है।" in srt


# ---------------------------------------------------------------- ffmpeg + music


def test_ffmpeg_command_shapes_captions_and_ducks_music(tmp_path) -> None:
    base = dict(
        frames_list="f.txt",
        narration="n.wav",
        captions="c.ass",
        output="o.mp4",
        duration=10,
        fps=30,
        preset="veryfast",
        crf=20,
        music_db=-20,
    )
    with_music = " ".join(ffmpeg_command(music=tmp_path / "m.mp3", **base))
    assert "ass=c.ass:shaping=complex" in with_music
    assert "sidechaincompress" in with_music and "-stream_loop -1" in with_music
    no_music = ffmpeg_command(music=None, **base)
    assert "1:a" in no_music and "sidechaincompress" not in " ".join(no_music)


def test_pick_music(tmp_path) -> None:
    assert pick_music(tmp_path, "v") is None
    for n in ("a.mp3", "b.wav", "notes.txt"):
        (tmp_path / n).write_bytes(b"x")
    pick = pick_music(tmp_path, "video-1")
    assert pick is not None and pick.suffix in (".mp3", ".wav")
    assert pick_music(tmp_path, "video-1") == pick  # deterministic


# ---------------------------------------------------------------- frames for every format


@pytest.fixture(scope="module")
def ctx(settings) -> Context:
    return Context.load(settings)


@pytest.mark.parametrize("fmt", list(CONTACT_PARAMS))
def test_every_card_renders_for_every_format(ctx, fmt) -> None:
    brief = ctx.brief(fmt, CONTACT_PARAMS[fmt], ctx.settings.demo.as_of, "hi")
    script = Script(
        video_id="t",
        title="",
        writer="t",
        sample=True,
        data_fingerprint="",
        brief=brief,
        sections=[],
    )
    rd = render_data(ctx, script)
    rd.scale = 0.2
    pids = brief.product_ids
    for kind in KINDS:
        params = {"heading": "शीर्षक", "body": "टेक्स्ट"} if kind in ("title", "notice") else {}
        if kind == "compare":
            params = {
                "a": pids[0],
                "b": pids[-1] if len(pids) > 1 else "tarang-t9",
                "category": "display",
            }
        img = draw_shot(rd, Shot(kind, "x", 0, 1, pids[0], params), 0.5)
        expected = (216, 384) if brief.orientation == "portrait" else (384, 216)
        assert img.size == expected


def test_formats_have_distinct_themes() -> None:
    assert len({(t.bg_top, t.accent, t.layout) for t in THEMES.values()}) == 5


# ---------------------------------------------------------------- shot plan


def test_shot_plan_tiles_timeline_and_uses_every_visual(ctx, tmp_settings) -> None:
    c2 = Context.load(tmp_settings)
    brief = c2.brief("best_under", {"band": 20000}, tmp_settings.demo.as_of, "hi")
    script, _r, _d = generate(c2, brief, writer="template")
    from channel.voice.narration import plan_sentences

    sents = plan_sentences(script, {})
    t = 0.5
    spans, sections, last = [], [], None
    for s in sents:
        if s.section_id != last:
            kind = next(x.kind for x in script.sections if x.id == s.section_id)
            from channel.voice.narration import SectionSpan

            sections.append(SectionSpan(s.section_id, kind, t, 0))
        s.start, s.end = t, t + 2
        t += 2.3
        sections[-1].end = s.end
        last = s.section_id
        spans.append(s)
    tl = Timeline(spans, sections, t + 1)
    shots = plan_shots(script, tl)
    assert shots[0].start == 0 and shots[-1].end == tl.duration
    assert all(a.end == b.start for a, b in itertools.pairwise(shots))
    kinds = {s.kind for s in shots}
    assert {
        "title",
        "notice",
        "weights",
        "product",
        "scorecard",
        "price_chart",
        "buyskip",
        "ranking",
        "outro",
    } <= kinds
    first_scorecards = [s for s in shots if s.kind == "scorecard"]
    assert first_scorecards[0].animate
    # Exactly one animated scorecard per product.
    animated = [s.product_id for s in first_scorecards if s.animate]
    assert len(animated) == len(set(animated))


# ---------------------------------------------------------------- end-to-end Short


@pytest.fixture
def small_settings(tmp_settings, tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=5",
            str(music / "bed.wav"),
        ],
        check=True,
    )
    r = tmp_settings.render.model_copy(update={"scale": 0.25, "anim_frames": 4})
    paths = tmp_settings.paths.model_copy(update={"music": music})
    return tmp_settings.model_copy(update={"render": r, "paths": paths})


def test_render_short_end_to_end(small_settings) -> None:
    ctx = Context.load(small_settings)
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, small_settings.demo.as_of, "hi")
    _s, _r, d = generate(ctx, brief, writer="template")
    result = render_video(ctx, d, tts_provider="espeak", provider=ToneTTS(0.06))
    info = probe(result.video)
    assert (info["width"], info["height"]) == (270, 480)
    assert info["has_audio"]
    assert 30 <= info["duration"] <= 50
    meta = json.loads((d / "render.json").read_text())
    assert meta["music"] == "bed.wav" and meta["orientation"] == "portrait"
    cost = json.loads((d / "cost.json").read_text())
    assert cost["total_inr"] == 0 and cost["lines"]["tts_chars_synthesized"] > 0
    for f in ("captions.ass", "captions.srt", "timeline.json", "narration.wav"):
        assert (d / f).exists()
    assert not (d / "build").exists()


def test_render_refuses_failed_factcheck(small_settings) -> None:
    ctx = Context.load(small_settings)
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, small_settings.demo.as_of, "hi")
    _s, _r, d = generate(ctx, brief, writer="template")
    rep = json.loads((d / "factcheck.json").read_text())
    rep["status"] = "fail"
    (d / "factcheck.json").write_text(json.dumps(rep))
    with pytest.raises(FactCheckBlocked):
        render_video(ctx, d, tts_provider="espeak", provider=ToneTTS())
    assert not (d / "video.mp4").exists()


def test_cli_render_and_proofs(small_settings, monkeypatch, capsys) -> None:
    from channel import cli

    monkeypatch.setattr(cli, "load_settings", lambda: small_settings)
    assert cli.main(["proofs"]) == 0
    assert (Path(small_settings.paths.out) / "test_frames" / "formats_contact_sheet.png").exists()
    assert "True" in capsys.readouterr().out
