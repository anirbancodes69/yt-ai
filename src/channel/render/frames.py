"""Code-rendered frames: title, notice, scorecard, price chart, ranked bars,
buy-if/skip-if, head-to-head, deal tag, outro — landscape and portrait.

Every number drawn here comes from the fact table or the data files directly,
never from the narration text. Every frame carries the "prices as of <date>"
footer, and a SAMPLE watermark when the data is sample.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter, MaxNLocator
from PIL import Image, ImageDraw, ImageFilter

from channel.data.models import Dataset, Product
from channel.data.pricing import DailyPrice
from channel.facts import Facts
from channel.fmt import group_indian, inr, long_date, num
from channel.render.fonts import FontSet
from channel.render.theme import RGB, Theme
from channel.scoring import ScoringConfig
from channel.scripts.models import Brief
from channel.scripts.templates import T as PHRASES

FORMAT_LABEL = {
    "hi": {
        "best_under": "बेस्ट फ़ोन लिस्ट",
        "head_to_head": "आमने-सामने",
        "launch_explainer": "लॉन्च एक्सप्लेनर",
        "deal_short": "डील अलर्ट",
        "monthly_refresh": "मंथली अपडेट",
    },
    "en": {
        "best_under": "Best phones list",
        "head_to_head": "Head to head",
        "launch_explainer": "Launch explainer",
        "deal_short": "Deal alert",
        "monthly_refresh": "Monthly update",
    },
}
SECURITY = {"hi": "सिक्योरिटी", "en": "Security"}


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


@dataclass
class RenderData:
    brief: Brief
    facts: Facts
    dataset: Dataset
    scoring: ScoringConfig
    theme: Theme
    fonts: FontSet
    text: dict[str, str]  # render strings for the language
    press_dir: Path
    history: dict[str, list[DailyPrice]]
    history_days: int
    months: list[str]
    scale: float = 1.0
    _bg_cache: dict[bool, Image.Image] = field(default_factory=dict)

    @property
    def lang(self) -> str:
        return self.brief.lang

    @property
    def sample(self) -> bool:
        return self.dataset.is_sample

    @property
    def date_str(self) -> str:
        return long_date(self.brief.as_of, self.months)

    def fact(self, ref: str) -> Any:
        f = self.facts.get(ref)
        return None if f is None else f.value

    def name(self, pid: str) -> str:
        p = self.dataset.product(pid)
        return f"{p.brand} {p.model}"


class Canvas:
    """One frame. Coordinates are in base units (1920x1080 or 1080x1920) and scaled."""

    def __init__(self, rd: RenderData, portrait: bool) -> None:
        self.rd, self.portrait, self.k = rd, portrait, rd.scale
        self.BW, self.BH = (1080, 1920) if portrait else (1920, 1080)
        self.W, self.H = self.px(self.BW), self.px(self.BH)
        self.t, self.f = rd.theme, rd.fonts
        self.img = self._background().copy()
        self.d = ImageDraw.Draw(self.img, "RGBA")

    # ------------------------------------------------------------------ primitives
    def px(self, v: float) -> int:
        return round(v * self.k)

    def text(self, xy, s: str, size: float, fill=None, *, bold=False, anchor="la") -> float:
        return (
            self.f.draw(
                self.d,
                (self.px(xy[0]), self.px(xy[1])),
                s,
                self.px(size),
                fill or self.t.text,
                bold=bold,
                anchor=anchor,
            )
            / self.k
        )

    def width(self, s: str, size: float, bold=False) -> float:
        return self.f.width(s, self.px(size), bold) / self.k

    def fit(self, s: str, size: float, max_w: float, bold=False, min_size=24) -> float:
        while size > min_size and self.width(s, size, bold) > max_w:
            size -= 2
        return size

    def rect(self, box, fill, radius=24, outline=None, width=0) -> None:
        x0, y0, x1, y1 = (self.px(v) for v in box)
        self.d.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=self.px(radius),
            fill=fill,
            outline=outline,
            width=self.px(width) if width else 0,
        )

    def paragraph(
        self,
        x,
        y,
        s: str,
        size: float,
        max_w: float,
        fill=None,
        bold=False,
        anchor="la",
        max_lines=8,
    ) -> float:
        lines = self.f.wrap(s, self.px(size), self.px(max_w), bold)[:max_lines]
        lh = self.f.line_height(self.px(size)) / self.k
        for i, line in enumerate(lines):
            self.text((x, y + i * lh), line, size, fill, bold=bold, anchor=anchor)
        return y + len(lines) * lh

    def bar(self, box, frac: float, color: RGB, track: RGB | None = None) -> None:
        x0, y0, x1, y1 = box
        self.rect(box, track or mix(self.t.card, self.t.bg_top, 0.5), radius=(y1 - y0) / 2)
        if frac > 0.002:
            self.rect((x0, y0, x0 + (x1 - x0) * min(frac, 1), y1), color, radius=(y1 - y0) / 2)

    def paste(self, im: Image.Image, box, *, contain=True) -> None:
        x0, y0, x1, y1 = (self.px(v) for v in box)
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        im = im.convert("RGBA")
        ratio = (
            min(bw / im.width, bh / im.height) if contain else max(bw / im.width, bh / im.height)
        )
        im = im.resize(
            (max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS
        )
        self.img.paste(im, (x0 + (bw - im.width) // 2, y0 + (bh - im.height) // 2), im)

    # ------------------------------------------------------------------ chrome
    def _background(self) -> Image.Image:
        cache = self.rd._bg_cache
        if self.portrait in cache:
            return cache[self.portrait]
        t = self.t
        grad = Image.new("RGB", (1, 256))
        for y in range(256):
            grad.putpixel((0, y), mix(t.bg_top, t.bg_bottom, y / 255))
        img = grad.resize((self.W, self.H))
        d = ImageDraw.Draw(img, "RGBA")
        if t.layout == "split":
            d.polygon(
                [(0, 0), (self.W * 0.5, 0), (self.W * 0.38, self.H), (0, self.H)],
                fill=(*t.accent, 38),
            )
            d.polygon(
                [(self.W * 0.5, 0), (self.W, 0), (self.W, self.H), (self.W * 0.38, self.H)],
                fill=(*t.accent2, 38),
            )
        elif t.layout == "ribbon":
            for i in range(6):
                off = self.px(140 * i)
                d.line([(self.W - off, 0), (self.W, off)], fill=(*t.accent, 40), width=self.px(10))
        elif t.layout == "pricetag":
            d.rectangle((0, 0, self.W, self.px(18)), fill=t.accent)
            d.rectangle((0, self.H - self.px(10), self.W, self.H), fill=t.accent2)
        elif t.layout == "movers":
            for i in range(0, self.W + self.H, self.px(90)):
                d.line([(i, 0), (i - self.H, self.H)], fill=(*t.accent, 14), width=self.px(3))
        else:  # countdown
            d.ellipse(
                (self.W * 0.62, -self.H * 0.3, self.W * 1.25, self.H * 0.55), fill=(*t.accent, 22)
            )
        self._chrome(img)
        cache[self.portrait] = img
        return img

    def _chrome(self, img: Image.Image) -> None:
        d = ImageDraw.Draw(img, "RGBA")
        save_img, save_d = getattr(self, "img", None), getattr(self, "d", None)
        self.img, self.d = img, d
        rd, t = self.rd, self.t
        label = FORMAT_LABEL[rd.lang][rd.brief.format]
        lw = self.width(label, 30, True)
        self.rect((48, 36, 48 + lw + 48, 92), t.accent, radius=28)
        self.text((72, 64), label, 30, t.bg_top, bold=True, anchor="lm")
        if t.layout == "ribbon":
            self.text(
                (self.BW - 60, 64), rd.text["launch"].upper(), 30, t.accent, bold=True, anchor="rm"
            )
        footer = rd.text["footer"].format(date=rd.date_str)
        fy = self.BH - 44
        size = self.fit(footer, 24 if not self.portrait else 22, self.BW - 96)
        self.text((48, fy), footer, size, t.muted, anchor="lm")
        if rd.sample:
            wm = rd.text["sample_watermark"]
            ww = self.width(wm, 24, True)
            x1 = self.BW - 48
            y0 = 110 if (t.layout == "ribbon" or self.portrait) else 36
            self.rect((x1 - ww - 40, y0, x1, y0 + 52), (200, 30, 30), radius=12)
            self.text((x1 - 20, y0 + 26), wm, 24, (255, 255, 255), bold=True, anchor="rm")
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            big = self.f.font("latin", self.px(220 if not self.portrait else 170), True)
            ld.text(
                (img.width / 2, img.height / 2),
                "SAMPLE",
                font=big,
                anchor="mm",
                fill=(255, 255, 255, 16),
            )
            layer = layer.rotate(18, resample=Image.BICUBIC)
            img.paste(layer, (0, 0), layer)
        self.img, self.d = save_img, save_d  # type: ignore[assignment]

    # ------------------------------------------------------------------ layout helpers
    @property
    def content(self) -> tuple[float, float, float, float]:
        """Area for cards; the bottom is reserved for captions and the footer."""
        return (80, 260, self.BW - 80, 1330) if self.portrait else (80, 130, self.BW - 80, 800)

    def heading(self, s: str, y: float | None = None, size: float = 56) -> float:
        x0, y0, x1, _ = self.content
        y = y0 if y is None else y
        size = self.fit(s, size, x1 - x0, bold=True)
        self.text((x0, y), s, size, bold=True)
        return y + size * 1.5

    def chip(self, x, y, s: str, fill: RGB, color: RGB | None = None, size=26) -> float:
        w = self.width(s, size, True)
        self.rect((x, y, x + w + 36, y + size * 1.8), fill, radius=size)
        self.text((x + 18, y + size * 0.9), s, size, color or self.t.bg_top, bold=True, anchor="lm")
        return x + w + 36

    def image(self) -> Image.Image:
        return self.img


# ---------------------------------------------------------------------- data helpers


def key_spec_line(rd: RenderData, p: Product, category: str) -> str:
    v = p.spec_value
    yrs = rd.text["years"]
    if category == "performance":
        return f"{v('chipset')} · AnTuTu {group_indian(int(v('antutu_v10')))} · {v('ram_gb')} GB"
    if category == "display":
        return (
            f'{num(v("display_size_in"))}" {str(v("display_type")).upper()} · '
            f"{v('refresh_rate_hz')}Hz · {group_indian(int(v('peak_brightness_nits')))} nits"
        )
    if category == "camera":
        s = f"{v('main_camera_mp')}MP" + (" OIS" if v("main_camera_ois") else "")
        return s + (f" · {v('ultrawide_mp')}MP UW" if v("ultrawide_mp") else "")
    if category == "battery":
        return f"{group_indian(int(v('battery_mah')))}mAh · {v('charging_w')}W"
    if category == "software":
        sec = v("security_updates_years")
        return f"OS {v('os_updates_years')} {yrs} · {SECURITY[rd.lang]} {sec} {yrs}"
    if category == "build":
        ip = str(v("ip_rating"))
        no_ip = PHRASES[rd.lang]["no_ip"]
        return f"{no_ip if ip == 'none' else ip.upper()} · {v('weight_g')} g"
    return ""


def press_image(rd: RenderData, p: Product, theme: Theme, size=(600, 900)) -> Image.Image:
    for name in p.press_images:
        path = rd.press_dir / name
        if path.exists():
            return Image.open(path)
    # Placeholder phone (no press image supplied, e.g. SAMPLE data).
    w, h = size
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(
        (w * 0.18, h * 0.04, w * 0.82, h * 0.96),
        radius=int(w * 0.09),
        fill=mix(theme.card, (0, 0, 0), 0.4),
        outline=theme.accent,
        width=6,
    )
    d.rounded_rectangle(
        (w * 0.22, h * 0.08, w * 0.78, h * 0.92),
        radius=int(w * 0.06),
        fill=mix(theme.accent, theme.bg_top, 0.55),
    )
    d.ellipse((w * 0.47, h * 0.095, w * 0.53, h * 0.095 + w * 0.06), fill=(10, 10, 10))
    rd.fonts.draw(d, (w / 2, h * 0.5), p.brand, int(w * 0.11), theme.text, bold=True, anchor="mm")
    if p.sample:
        rd.fonts.draw(d, (w / 2, h * 0.6), "SAMPLE", int(w * 0.07), theme.text, anchor="mm")
    return im.filter(ImageFilter.SMOOTH)


def price_chart_image(rd: RenderData, pid: str, w_px: int, h_px: int) -> Image.Image:
    hist = rd.history.get(pid, [])
    t = rd.theme
    latin = font_manager.FontProperties(fname=str(rd.fonts.path("latin", False)))
    fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
    portrait_chart = w_px < h_px * 1.6
    tick_pt = max(4.0, min(w_px, h_px * 1.8) / (60 if portrait_chart else 90))
    # Margins sized from the tick font so "₹22,500" and dates never clip.
    left = min(0.35, (tick_pt * 1.39 * 8 + 12) / w_px)
    bottom = min(0.3, (tick_pt * 1.39 * 2.2) / h_px)
    ax = fig.add_axes((left, bottom, 0.96 - left, 0.94 - bottom))
    fig.patch.set_alpha(0)
    ax.set_facecolor((0, 0, 0, 0))
    color = tuple(c / 255 for c in t.accent2)
    xs = [h.on for h in hist]
    ys = [h.price_inr for h in hist]
    if xs:
        ax.plot(
            xs,
            ys,
            color=color,
            linewidth=max(2, w_px / 300),
            marker="o",
            markersize=max(3, w_px / 220),
        )
        ax.fill_between(xs, ys, min(ys) * 0.97, color=color, alpha=0.15)
        lo, hi = min(ys), max(ys)
        pad = max((hi - lo) * 0.25, hi * 0.03)
        ax.set_ylim(lo - pad, hi + pad)
        ax.annotate(
            inr(ys[-1]),
            (xs[-1], ys[-1]),
            textcoords="offset points",
            xytext=(-10, 14),
            ha="right",
            color=tuple(c / 255 for c in t.text),
            fontproperties=latin,
            fontsize=tick_pt * 1.3,
            fontweight="bold",
        )
    muted = tuple(c / 255 for c in t.muted)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(muted)
    ax.tick_params(colors=muted, labelsize=tick_pt)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontproperties(latin)
        lbl.set_fontsize(tick_pt)
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: inr(int(v))))
    ax.xaxis.set_major_locator(MaxNLocator(4 if w_px > h_px * 1.6 else 3))
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda v, _p: matplotlib.dates.num2date(v).strftime("%d/%m"))
    )
    ax.grid(axis="y", color=muted, alpha=0.2)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


# ---------------------------------------------------------------------- cards


def card_title(c: Canvas, heading: str, subtitle: str | None = None) -> None:
    rd, t = c.rd, c.t
    x0, y0, x1, y1 = c.content
    cy = (y0 + y1) / 2 - (120 if not c.portrait else 160)
    size = c.fit(heading, 96 if not c.portrait else 84, x1 - x0, bold=True)
    lines = c.f.wrap(heading, c.px(size), c.px(x1 - x0), True)
    lh = c.f.line_height(c.px(size)) / c.k
    for i, line in enumerate(lines[:3]):
        c.text(((x0 + x1) / 2, cy + i * lh), line, size, bold=True, anchor="ma")
    y = cy + len(lines[:3]) * lh + 30
    if subtitle:
        c.text(((x0 + x1) / 2, y), subtitle, 40, t.muted, anchor="ma")
        y += 80
    chip = rd.text["price_as_of"].format(date=rd.date_str)
    w = c.width(chip, 28, True) + 36
    c.chip((x0 + x1) / 2 - w / 2, y + 10, chip, t.accent)


def card_notice(c: Canvas, heading: str, body: str) -> None:
    x0, y0, x1, y1 = c.content
    c.rect((x0, y0 + 40, x1, y1 - 20), (*c.t.card, 235), radius=36)
    c.text((x0 + 60, y0 + 90), heading, 54, c.t.accent, bold=True)
    c.paragraph(x0 + 60, y0 + 190, body, 44 if not c.portrait else 46, x1 - x0 - 120)


def card_weights(c: Canvas, progress: float) -> None:
    rd = c.rd
    c.heading(rd.text["weights"])
    x0, y0, x1, y1 = c.content
    cats = list(rd.scoring.categories.items())
    top = y0 + 110
    row = (y1 - top) / len(cats)
    label_w = 380 if not c.portrait else 330
    for i, (name, cat) in enumerate(cats):
        y = top + i * row
        c.text((x0, y + row * 0.45), cat.label.get(rd.lang, name), 40, anchor="lm")
        frac = cat.weight / max(x.weight for _n, x in cats) * ease_out(progress)
        c.bar((x0 + label_w, y + row * 0.25, x1 - 160, y + row * 0.65), frac, c.t.accent)
        c.text((x1, y + row * 0.45), f"{round(cat.weight * 100)}%", 40, bold=True, anchor="rm")


def _rank_badge(c: Canvas, x, y, r, rank: int | None) -> None:
    if rank is None:
        return
    c.d.ellipse((c.px(x - r), c.px(y - r), c.px(x + r), c.px(y + r)), fill=c.t.accent)
    c.text((x, y), f"#{rank}", r * 0.9, c.t.bg_top, bold=True, anchor="mm")


def card_product(c: Canvas, pid: str, *, verdict: bool = False) -> None:
    rd, t = c.rd, c.t
    p = rd.dataset.product(pid)
    band = rd.brief.band_inr
    rank = rd.fact(f"ranking.{band}.{pid}.rank") if band else None
    price = rd.fact(f"prices.{pid}.current")
    overall = rd.fact(f"scores.{pid}.overall")
    x0, y0, x1, y1 = c.content
    if c.portrait:
        img_box = (x0 + 180, y0 + 20, x1 - 180, y0 + 560)
        tx, ty, tw = x0, y0 + 600, x1 - x0
    else:
        img_box = (x0 + 20, y0 + 10, x0 + 560, y1 - 10)
        tx, ty, tw = x0 + 640, y0 + 40, x1 - x0 - 640
    c.paste(press_image(rd, p, t), img_box)
    if verdict:
        label = f"{rd.text['verdict']} · {rd.text['winner']}"
        c.chip(tx, ty, label, t.gold, (20, 20, 20), size=32)
        ty += 90
    elif t.layout == "countdown" and rank is not None:
        _rank_badge(c, img_box[0] + 70, img_box[1] + 70, 62, rank)
    name_size = c.fit(c.rd.name(pid), 88, tw, bold=True)
    c.text((tx, ty), rd.name(pid), name_size, bold=True)
    ty += name_size * 1.45
    if rank is not None and t.layout != "countdown":
        c.chip(tx, ty, f"{rd.text['rank']} #{rank}", t.accent2, size=28)
        ty += 80
    if price is not None:
        c.text((tx, ty), inr(price), 96, t.accent, bold=True)
        ty += 130
        c.chip(tx, ty, rd.text["price_as_of"].format(date=rd.date_str), t.card, t.muted, size=26)
        ty += 90
    if overall is not None:
        c.text((tx, ty), f"{num(overall)}", 80, bold=True)
        w = c.width(f"{num(overall)}", 80, True)
        c.text((tx + w + 10, ty + 34), rd.text["score_out_of"], 40, t.muted)
        c.bar((tx, ty + 120, tx + min(tw, 760), ty + 146), overall / 10, t.accent)


def card_scorecard(c: Canvas, pid: str, progress: float, highlight: str | None = None) -> None:
    rd, t = c.rd, c.t
    p = rd.dataset.product(pid)
    y = c.heading(rd.name(pid))
    x0, _y0, x1, y1 = c.content
    cats = list(rd.scoring.categories.items())
    row = (y1 - y) / len(cats)
    label_w = 420 if not c.portrait else 0
    for i, (name, cat) in enumerate(cats):
        ry = y + i * row
        score = rd.fact(f"scores.{pid}.{name}") or 0
        color = (
            t.good
            if score >= rd.scoring.ranking.strength_threshold
            else (t.bad if score <= rd.scoring.ranking.weakness_threshold else t.accent)
        )
        if highlight == name:
            c.rect((x0 - 20, ry, x1 + 20, ry + row - 8), (*t.accent, 40), radius=18)
        if c.portrait:
            c.text((x0, ry + 8), cat.label.get(rd.lang, name), 40, bold=True)
            c.text((x1, ry + 8), f"{num(score)}", 40, bold=True, anchor="ra")
            c.bar(
                (x0, ry + row * 0.42, x1, ry + row * 0.42 + 22),
                score / 10 * ease_out(progress),
                color,
            )
            c.text((x0, ry + row * 0.62), key_spec_line(rd, p, name), 28, t.muted)
        else:
            c.text((x0, ry + row * 0.3), cat.label.get(rd.lang, name), 38, bold=True, anchor="lm")
            c.text((x0, ry + row * 0.72), key_spec_line(rd, p, name), 24, t.muted, anchor="lm")
            c.bar(
                (x0 + label_w + 140, ry + row * 0.3, x1 - 120, ry + row * 0.3 + 26),
                score / 10 * ease_out(progress),
                color,
            )
            c.text((x1, ry + row * 0.35), f"{num(score)}", 40, bold=True, anchor="rm")


def card_price_chart(c: Canvas, pid: str) -> None:
    rd = c.rd
    y = c.heading(
        f"{rd.name(pid)} — {rd.text['price_history'].format(days=rd.history_days)}", size=48
    )
    x0, _y0, x1, y1 = c.content
    chart = price_chart_image(rd, pid, c.px(x1 - x0), c.px(y1 - y - 70))
    c.paste(chart, (x0, y, x1, y1 - 70))
    c.chip(x0, y1 - 56, rd.text["price_as_of"].format(date=rd.date_str), c.t.accent, size=26)


def card_buyskip(c: Canvas, pid: str) -> None:
    rd, t = c.rd, c.t
    from channel.scoring import score_product

    s = score_product(rd.dataset.product(pid), rd.scoring)
    phr = PHRASES[rd.lang]
    buys = [phr["buy"][x] for x in s.strengths] or [rd.text["no_strength"]]
    skips = [phr["skip"][x] for x in s.weaknesses] or [rd.text["balanced"]]
    y = c.heading(rd.name(pid))
    x0, _y0, x1, y1 = c.content
    if c.portrait:
        boxes = [(x0, y, x1, (y + y1) / 2 - 15), (x0, (y + y1) / 2 + 15, x1, y1)]
    else:
        mid = (x0 + x1) / 2
        boxes = [(x0, y, mid - 20, y1), (mid + 20, y, x1, y1)]
    for box, title, items, color in (
        (boxes[0], rd.text["buy_if"], buys, t.good),
        (boxes[1], rd.text["skip_if"], skips, t.bad),
    ):
        c.rect(box, (*t.card, 240), radius=30, outline=color, width=4)
        c.text((box[0] + 40, box[1] + 40), title, 50, color, bold=True)
        yy = box[1] + 140
        for item in items:
            c.d.ellipse(
                (c.px(box[0] + 44), c.px(yy + 18), c.px(box[0] + 64), c.px(yy + 38)), fill=color
            )
            yy = c.paragraph(box[0] + 84, yy, item, 42, box[2] - box[0] - 120) + 20


def card_ranking(c: Canvas, pids: tuple[str, ...], progress: float, winner: str | None) -> None:
    rd, t = c.rd, c.t
    band = rd.brief.band_inr
    title = rd.text["verdict"]
    if band:
        title = f"{rd.text['verdict']} · {inr(band)}"
    y = c.heading(title)
    x0, _y0, x1, y1 = c.content
    row = min(130, (y1 - y) / max(1, len(pids)))
    name_w = 470 if not c.portrait else 0
    for i, pid in enumerate(pids):
        ry = y + i * row
        overall = rd.fact(f"scores.{pid}.overall") or 0
        price = rd.fact(f"prices.{pid}.current")
        color = t.gold if pid == winner else t.accent
        delay = i * 0.08
        prog = ease_out((progress - delay) / max(0.2, 1 - delay))
        label = f"#{i + 1} {rd.name(pid)}"
        if c.portrait:
            c.text((x0, ry), label, 38, bold=True)
            c.bar((x0, ry + 56, x1 - 120, ry + 82), overall / 10 * prog, color)
            c.text((x1, ry + 70), num(overall), 36, bold=True, anchor="rm")
        else:
            c.text((x0, ry + row * 0.4), label, 38, bold=True, anchor="lm")
            if price is not None:
                c.text((x0, ry + row * 0.78), inr(price), 26, t.muted, anchor="lm")
            c.bar(
                (x0 + name_w, ry + row * 0.3, x1 - 140, ry + row * 0.3 + 34),
                overall / 10 * prog,
                color,
            )
            c.text((x1, ry + row * 0.4), num(overall), 40, bold=True, anchor="rm")
        if t.layout == "movers":
            _mover(c, pid, x1 + 10 if not c.portrait else x1 - 60, ry + 40)


def _mover(c: Canvas, pid: str, x: float, y: float) -> None:
    band = c.rd.brief.band_inr
    now = c.rd.fact(f"ranking.{band}.{pid}.rank")
    prev = c.rd.fact(f"ranking_prev.{band}.{pid}.rank")
    if now is None:
        return
    if prev is None:
        c.text((x, y), c.rd.text["new"], 24, c.t.gold, bold=True, anchor="lm")
        return
    k = c.px
    if prev > now:
        c.d.polygon(
            [(k(x), k(y + 12)), (k(x + 24), k(y + 12)), (k(x + 12), k(y - 10))], fill=c.t.good
        )
    elif prev < now:
        c.d.polygon(
            [(k(x), k(y - 10)), (k(x + 24), k(y - 10)), (k(x + 12), k(y + 12))], fill=c.t.bad
        )
    else:
        c.d.rectangle((k(x), k(y - 2), k(x + 24), k(y + 4)), fill=c.t.muted)


def card_compare(c: Canvas, a: str, b: str, progress: float, category: str | None) -> None:
    rd, t = c.rd, c.t
    x0, y0, x1, y1 = c.content
    mid = (x0 + x1) / 2
    gap = c.width(rd.text["vs"], 40) / 2 + 30
    size = min(
        c.fit(rd.name(a), 52, (x1 - x0) / 2 - gap, True),
        c.fit(rd.name(b), 52, (x1 - x0) / 2 - gap, True),
    )
    c.text((mid - gap, y0 + 10), rd.name(a), size, t.accent, bold=True, anchor="ra")
    c.text((mid, y0 + 18), rd.text["vs"], 40, t.muted, anchor="ma")
    c.text((mid + gap, y0 + 10), rd.name(b), size, t.accent2, bold=True, anchor="la")
    cats = list(rd.scoring.categories.items())
    top = y0 + 110
    row = (y1 - top) / len(cats)
    for i, (name, cat) in enumerate(cats):
        ry = top + i * row
        sa = rd.fact(f"scores.{a}.{name}") or 0
        sb = rd.fact(f"scores.{b}.{name}") or 0
        if category == name:
            c.rect((x0 - 20, ry, x1 + 20, ry + row - 8), (*t.text, 22), radius=18)
        prog = ease_out(progress)
        half = (x1 - x0) / 2 - 170
        c.text((mid, ry + row * 0.42), cat.label.get(rd.lang, name), 34, bold=True, anchor="mm")
        ya, yb = ry + row * 0.28, ry + row * 0.28 + 30
        c.rect((mid - 140 - half * sa / 10 * prog, ya, mid - 140, yb), t.accent, radius=15)
        c.rect((mid + 140, ya, mid + 140 + half * sb / 10 * prog, yb), t.accent2, radius=15)
        c.text((x0, ry + row * 0.42), num(sa), 36, bold=True, anchor="lm")
        c.text((x1, ry + row * 0.42), num(sb), 36, bold=True, anchor="rm")


def card_deal(c: Canvas, pid: str) -> None:
    rd, t = c.rd, c.t
    p = rd.dataset.product(pid)
    x0, y0, x1, _y1 = c.content
    was = rd.fact(f"prices.{pid}.highest_90d")
    now = rd.fact(f"prices.{pid}.current")
    c.paste(press_image(rd, p, t), (x0 + 200, y0, x1 - 200, y0 + 560))
    c.text(((x0 + x1) / 2, y0 + 600), rd.name(pid), 80, bold=True, anchor="ma")
    tag = (x0 + 60, y0 + 740, x1 - 60, y0 + 1040)
    c.rect(tag, t.accent, radius=40)
    c.text(((x0 + x1) / 2, tag[1] + 30), rd.text["deal"], 44, t.bg_top, bold=True, anchor="ma")
    if now is not None:
        c.text(((x0 + x1) / 2, tag[1] + 110), inr(now), 120, t.bg_top, bold=True, anchor="ma")
    if was is not None and now is not None and was > now:
        s = f"{rd.text['was']} {inr(was)}"
        w = c.text(((x0 + x1) / 2, tag[3] + 30), s, 48, t.accent2, bold=True, anchor="ma")
        yy = tag[3] + 30 + 30
        c.d.line(
            (c.px((x0 + x1) / 2 - w / 2), c.px(yy), c.px((x0 + x1) / 2 + w / 2), c.px(yy)),
            fill=t.accent2,
            width=c.px(6),
        )


def card_outro(c: Canvas) -> None:
    rd, t = c.rd, c.t
    x0, y0, x1, y1 = c.content
    cy = (y0 + y1) / 2
    c.rect(((x0 + x1) / 2 - 330, cy - 160, (x0 + x1) / 2 + 330, cy - 20), t.bad, radius=70)
    c.text(
        ((x0 + x1) / 2, cy - 90), rd.text["subscribe"], 60, (255, 255, 255), bold=True, anchor="mm"
    )
    c.text(((x0 + x1) / 2, cy + 60), rd.text["links"], 48, t.muted, anchor="ma")
