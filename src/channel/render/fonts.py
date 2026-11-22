"""Fonts and text drawing with correct Devanagari shaping.

Pillow is used with the raqm layout engine (HarfBuzz + FriBiDi), which shapes
conjuncts and places matras correctly. Noto Sans Devanagari has no Latin letters,
so text is split into script runs: Devanagari runs use Noto Sans Devanagari,
Latin runs use Noto Sans, and digits/punctuation stay with the surrounding run.
Runs share a baseline.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageDraw, ImageFont, features

SYSTEM_FONT_DIR = Path("/usr/share/fonts/truetype/noto")
FILES = {
    ("deva", False): "NotoSansDevanagari-Regular.ttf",
    ("deva", True): "NotoSansDevanagari-Bold.ttf",
    ("latin", False): "NotoSans-Regular.ttf",
    ("latin", True): "NotoSans-Bold.ttf",
}


class FontError(RuntimeError):
    pass


def require_raqm() -> None:
    if not features.check("raqm"):
        raise FontError(
            "Pillow was built without raqm; Devanagari would render incorrectly. "
            "Use the Docker image (it ships libraqm)."
        )


def _script(ch: str) -> str | None:
    cp = ord(ch)
    if 0x0900 <= cp <= 0x097F or 0xA8E0 <= cp <= 0xA8FF or 0x1CD0 <= cp <= 0x1CFF:
        return "deva"
    if ch.isalpha():
        return "latin"
    if cp in (0x200C, 0x200D):  # ZWNJ / ZWJ belong to Devanagari shaping
        return "deva"
    # Noto Sans Devanagari lacks most non-ASCII symbols (·, •, —, ″) and $ & @ ` :
    # draw those with the Latin font. ₹ exists in both fonts.
    if (cp >= 0x80 and ch != "₹") or ch in "$&@`":
        return "latin"
    return None  # digits, spaces, ASCII punctuation, ₹: neutral


def script_runs(text: str) -> list[tuple[str, str]]:
    """[(script, run_text)]; neutral characters join the current run."""
    runs: list[tuple[str, str]] = []
    current: str | None = None
    buf = ""
    for ch in text:
        sc = _script(ch)
        if sc is None or current is None or sc == current:
            current = current or sc
            buf += ch
        else:
            runs.append((current, buf))
            current, buf = sc, ch
    if buf:
        runs.append((current or "deva", buf))
    return runs


@dataclass(frozen=True)
class FontSet:
    dirs: tuple[Path, ...]

    def path(self, script: str, bold: bool) -> Path:
        name = FILES[(script, bold)]
        for d in self.dirs:
            if (d / name).exists():
                return d / name
        raise FontError(f"font {name} not found in {[str(d) for d in self.dirs]}")

    @functools.lru_cache(maxsize=256)  # noqa: B019 - FontSet is frozen and long-lived
    def font(self, script: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(
            str(self.path(script, bold)), size, layout_engine=ImageFont.Layout.RAQM
        )

    # ------------------------------------------------------------------ measuring/drawing
    def width(self, text: str, size: int, bold: bool = False) -> float:
        return sum(self.font(sc, size, bold).getlength(t) for sc, t in script_runs(text))

    def draw(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: str,
        size: int,
        fill,
        *,
        bold: bool = False,
        anchor: str = "la",  # l/m/r + a(top)/s(baseline)/m(middle)
    ) -> float:
        """Draw mixed-script text; returns its width. Anchor works like Pillow's
        horizontal l/m/r with vertical a (top of line), m (middle) or s (baseline)."""
        w = self.width(text, size, bold)
        x, y = xy
        if anchor[0] == "m":
            x -= w / 2
        elif anchor[0] == "r":
            x -= w
        deva = self.font("deva", size, bold)
        ascent, descent = deva.getmetrics()
        baseline = {"a": y + ascent, "m": y + (ascent - descent) / 2, "s": y}[anchor[1]]
        for sc, t in script_runs(text):
            f = self.font(sc, size, bold)
            draw.text((x, baseline), t, font=f, fill=fill, anchor="ls")
            x += f.getlength(t)
        return w

    def wrap(self, text: str, size: int, max_width: float, bold: bool = False) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if current and self.width(trial, size, bold) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines

    def line_height(self, size: int) -> int:
        a, d = self.font("deva", size).getmetrics()
        return int((a + d) * 1.05)


def font_set(asset_dir: Path) -> FontSet:
    """Fonts in assets/fonts/ override the system Noto fonts."""
    require_raqm()
    return FontSet((asset_dir, SYSTEM_FONT_DIR))
