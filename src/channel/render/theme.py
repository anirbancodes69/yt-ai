"""Per-format visual identity, so the five formats are recognisably different."""

from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    name: str
    bg_top: RGB
    bg_bottom: RGB
    card: RGB
    text: RGB
    muted: RGB
    accent: RGB  # primary highlight (bars, badges)
    accent2: RGB  # secondary (second product, price line)
    good: RGB = (46, 204, 113)
    bad: RGB = (231, 76, 60)
    gold: RGB = (255, 196, 0)
    layout: str = "countdown"  # countdown | split | ribbon | pricetag | movers


THEMES: dict[str, Theme] = {
    # Countdown list: deep navy, cyan accents, big rank badges.
    "best_under": Theme(
        "best_under",
        (10, 18, 40),
        (22, 40, 82),
        (28, 44, 84),
        (245, 248, 255),
        (160, 175, 210),
        (0, 200, 255),
        (255, 145, 77),
        layout="countdown",
    ),
    # Head-to-head: charcoal split, red vs blue corners.
    "head_to_head": Theme(
        "head_to_head",
        (24, 24, 28),
        (40, 40, 48),
        (48, 48, 58),
        (250, 250, 250),
        (170, 170, 180),
        (235, 64, 52),
        (52, 120, 246),
        layout="split",
    ),
    # Launch explainer: violet gradient, diagonal LAUNCH ribbon.
    "launch_explainer": Theme(
        "launch_explainer",
        (34, 10, 58),
        (88, 28, 135),
        (60, 24, 98),
        (255, 247, 255),
        (205, 180, 230),
        (255, 92, 205),
        (120, 220, 255),
        layout="ribbon",
    ),
    # Deal Short: warm black, yellow price tag, vertical.
    "deal_short": Theme(
        "deal_short",
        (20, 14, 8),
        (64, 30, 6),
        (54, 34, 14),
        (255, 252, 240),
        (230, 200, 160),
        (255, 214, 0),
        (255, 82, 82),
        layout="pricetag",
    ),
    # Monthly refresh: forest green, rank-movement arrows.
    "monthly_refresh": Theme(
        "monthly_refresh",
        (6, 32, 26),
        (14, 72, 56),
        (20, 70, 56),
        (240, 255, 248),
        (160, 210, 190),
        (60, 230, 160),
        (255, 205, 80),
        layout="movers",
    ),
}


def theme_for(fmt: str) -> Theme:
    return THEMES[fmt]
