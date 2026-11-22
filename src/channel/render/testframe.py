"""Proof that Devanagari is shaped correctly: the same strings drawn with the raqm
layout engine (correct) and Pillow's BASIC engine (broken), side by side."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from channel.render.fonts import FontSet

SAMPLES = (
    "क्षमता, श्रेणी, स्मार्टफ़ोन",  # conjuncts (क्ष, श्र, स्म)
    "कि की कु कृ प्रि",  # i-matra must sit LEFT of the consonant
    "Tarang T9 की कीमत ₹18,999 है।",  # mixed Latin + Devanagari + rupee
)


def render_test_frame(fonts: FontSet, out: Path) -> dict[str, list[float]]:
    """Write the comparison PNG; return the measured widths per engine (shaping changes them)."""
    size = 64
    img = Image.new("RGB", (1920, 1080), (250, 250, 250))
    d = ImageDraw.Draw(img)
    fonts.draw(
        d,
        (60, 40),
        "Devanagari shaping test — raqm (correct) vs BASIC (broken)",
        40,
        (20, 20, 20),
        bold=True,
    )
    deva = str(fonts.path("deva", False))
    basic = ImageFont.truetype(deva, size, layout_engine=ImageFont.Layout.BASIC)
    widths: dict[str, list[float]] = {"raqm": [], "basic": []}
    y = 140
    for text in SAMPLES:
        fonts.draw(d, (60, y), "raqm:", 32, (0, 120, 60), bold=True)
        widths["raqm"].append(fonts.draw(d, (260, y), text, size, (10, 10, 10)))
        fonts.draw(d, (60, y + 110), "BASIC:", 32, (180, 40, 40), bold=True)
        d.text((260, y + 110), text, font=basic, fill=(120, 120, 120))
        widths["basic"].append(basic.getlength(text))
        y += 300
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return widths


CAPTION_SAMPLE = "आधारित, कीमत स्पेसिफिकेशन, क्षमता"


def render_caption_test(out: Path) -> dict[str, bytes]:
    """Burn the same ASS caption with libass 'simple' and 'complex' shaping and stack
    them (top = simple/broken, bottom = complex/correct). Returns raw pixels per mode."""
    from channel.render.captions import write_ass
    from channel.render.theme import THEMES
    from channel.voice.narration import Sentence, Timeline

    tl = Timeline([Sentence("s", 0, CAPTION_SAMPLE, CAPTION_SAMPLE, 0.0, 3.0)], [], 3.0)
    images = {}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "c.ass").write_text(
            write_ass(tl, THEMES["best_under"], width=1280, height=360, portrait=False), "utf-8"
        )
        for mode in ("simple", "complex"):
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x101828:s=1280x360:d=3",
                    "-vf",
                    f"ass=c.ass:shaping={mode}",
                    "-ss",
                    "2.9",
                    "-frames:v",
                    "1",
                    f"{mode}.png",
                ],
                cwd=d,
                check=True,
            )
            images[mode] = Image.open(d / f"{mode}.png").convert("RGB")
    sheet = Image.new("RGB", (1280, 720))
    sheet.paste(images["simple"], (0, 0))
    sheet.paste(images["complex"], (0, 360))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return {m: im.tobytes() for m, im in images.items()}
