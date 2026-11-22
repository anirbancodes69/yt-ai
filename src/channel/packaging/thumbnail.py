"""Code-generated thumbnails, one layout per format.

Landscape 1280x720 (YouTube's size), portrait 1080x1920 cover for the Short.
Large, high-contrast text (readable at 160 px wide), the phone (press image or
placeholder), the current price, and the winner's rank. Every number is read from
the fact table. SAMPLE data gets a badge and a watermark.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from channel.fmt import inr, long_date
from channel.packaging.models import LangText
from channel.render.frames import RenderData, mix, press_image
from channel.render.theme import RGB, Theme

LANDSCAPE = (1280, 720)
PORTRAIT = (1080, 1920)


class _Pen:
    def __init__(self, rd: RenderData, size: tuple[int, int]) -> None:
        self.rd, self.t, self.f = rd, rd.theme, rd.fonts
        self.W, self.H = size
        grad = Image.new("RGB", (1, 256))
        for y in range(256):
            grad.putpixel((0, y), mix(self.t.bg_top, self.t.bg_bottom, y / 255))
        self.img = grad.resize(size)
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def text(
        self, xy, s: str, size: int, fill: RGB | None = None, *, bold=True, anchor="la"
    ) -> float:
        return self.f.draw(self.d, xy, s, size, fill or self.t.text, bold=bold, anchor=anchor)

    def fit(self, s: str, size: int, max_w: float, min_size: int = 30) -> int:
        while size > min_size and self.f.width(s, size, True) > max_w:
            size -= 4
        return size

    def chip(
        self, xy, s: str, size: int, fill: RGB, color: RGB | None = None
    ) -> tuple[float, float]:
        w = self.f.width(s, size, True)
        x, y = xy
        self.d.rounded_rectangle(
            (x, y, x + w + size, y + size * 1.7), radius=int(size * 0.85), fill=fill
        )
        self.text((x + size / 2, y + size * 0.85), s, size, color or self.t.bg_top, anchor="lm")
        return w + size, size * 1.7

    def phone(self, pid: str, box: tuple[int, int, int, int]) -> None:
        im = press_image(self.rd, self.rd.dataset.product(pid), self.t).convert("RGBA")
        x0, y0, x1, y1 = box
        r = min((x1 - x0) / im.width, (y1 - y0) / im.height)
        im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        self.img.paste(im, (x0 + (x1 - x0 - im.width) // 2, y0 + (y1 - y0 - im.height) // 2), im)

    def decorate(self) -> None:
        t, d = self.t, self.d
        if t.layout == "split":
            d.polygon(
                [(0, 0), (self.W * 0.5, 0), (self.W * 0.38, self.H), (0, self.H)],
                fill=(*t.accent, 60),
            )
            d.polygon(
                [(self.W * 0.5, 0), (self.W, 0), (self.W, self.H), (self.W * 0.38, self.H)],
                fill=(*t.accent2, 60),
            )
        elif t.layout == "ribbon":
            for i in range(7):
                d.line([(self.W - 160 * i, 0), (self.W, 160 * i)], fill=(*t.accent, 55), width=14)
        elif t.layout == "pricetag":
            d.rectangle((0, 0, self.W, 26), fill=t.accent)
            d.rectangle((0, self.H - 16, self.W, self.H), fill=t.accent2)
        elif t.layout == "movers":
            for i in range(0, self.W + self.H, 110):
                d.line([(i, 0), (i - self.H, self.H)], fill=(*t.accent, 22), width=4)
        else:
            d.ellipse(
                (self.W * 0.55, -self.H * 0.4, self.W * 1.3, self.H * 0.7), fill=(*t.accent, 40)
            )

    def sample_marks(self, label: str) -> None:
        w = self.f.width(label, 30, True)
        y = 28
        self.d.rounded_rectangle(
            (self.W - w - 70, y, self.W - 24, y + 56), radius=14, fill=(200, 30, 30)
        )
        self.text((self.W - 47, y + 28), label, 30, (255, 255, 255), anchor="rm")
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        big = self.f.font("latin", 230 if self.W > self.H else 200, True)
        ImageDraw.Draw(layer).text(
            (self.W / 2, self.H / 2), "SAMPLE", font=big, anchor="mm", fill=(255, 255, 255, 22)
        )
        layer = layer.rotate(18, resample=Image.BICUBIC)
        self.img.paste(layer, (0, 0), layer)


def render_thumbnail(rd: RenderData, text: LangText, out: Path, *, months: list[str]) -> Path:
    brief, t = rd.brief, rd.theme
    portrait = brief.orientation == "portrait"
    pen = _Pen(rd, PORTRAIT if portrait else LANDSCAPE)
    pen.decorate()
    th = text.thumbnail
    band = brief.band_inr
    fields = {
        "band": inr(band) if band else "",
        "month_year": f"{months[brief.as_of.month - 1]} {brief.as_of.year}",
    }
    first = brief.product_ids[0]

    def price(pid: str) -> int | None:
        v = rd.fact(f"prices.{pid}.current")
        return int(v) if v is not None else None

    W, H = pen.W, pen.H
    fmt = brief.format
    if fmt == "best_under":
        head, sub = th["best_under_head"].format(**fields), th["best_under_sub"]
        _headline(pen, head, sub, left=(60, 90, 700), portrait=False)
        pen.phone(first, (730, 70, 1230, 560))
        rank = rd.fact(f"ranking.{band}.{first}.rank")
        if rank is not None:
            pen.chip(
                (60, 500), f"{th['badge_rank'].format(rank=rank)} {rd.name(first)}", 44, t.accent2
            )
        _price_badge(pen, price(first), (720, 565), 56)
    elif fmt == "head_to_head":
        a, b = brief.product_ids[:2]
        size = pen.fit(th["head_to_head_head"], 130, W - 120)
        pen.text((W / 2, 40), th["head_to_head_head"], size, anchor="ma")
        pen.phone(a, (80, 170, 520, 500))
        pen.phone(b, (760, 170, 1200, 500))
        pen.d.ellipse((W / 2 - 80, 275, W / 2 + 80, 435), fill=t.gold)
        pen.text((W / 2, 355), rd.text["vs"], 56, (20, 20, 20), anchor="mm")
        for pid, cx, col in ((a, 300, t.accent), (b, 980, t.accent2)):
            s = pen.fit(rd.name(pid), 60, 520)
            pen.text((cx, 515), rd.name(pid), s, col, anchor="ma")
            p = price(pid)
            if p is not None:
                pen.text((cx, 595), inr(p), 46, t.text, bold=True, anchor="ma")
    elif fmt == "launch_explainer":
        head, sub = th["launch_explainer_head"], th["launch_explainer_sub"]
        _headline(pen, head, sub, left=(60, 90, 700), portrait=False, accent_head=True)
        pen.phone(first, (730, 70, 1230, 560))
        pen.chip((60, 500), rd.name(first), 48, t.accent2)
        _price_badge(pen, price(first), (720, 565), 56)
    elif fmt == "monthly_refresh":
        head, sub = (
            th["monthly_refresh_head"].format(**fields),
            th["monthly_refresh_sub"].format(**fields),
        )
        _headline(pen, head, sub, left=(60, 90, 700), portrait=False)
        pen.phone(first, (730, 70, 1230, 560))
        pen.chip((60, 500), f"{th['badge_rank'].format(rank=1)} {rd.name(first)}", 44, t.accent)
        _price_badge(pen, price(first), (720, 565), 56)
    else:  # deal_short, portrait cover
        pen.text(
            (W / 2, 150),
            th["deal_short_head"],
            pen.fit(th["deal_short_head"], 150, W - 100),
            t.accent,
            anchor="ma",
        )
        pen.phone(first, (200, 330, W - 200, 1050))
        pen.text((W / 2, 1090), rd.name(first), pen.fit(rd.name(first), 110, W - 100), anchor="ma")
        pen.d.rounded_rectangle((90, 1260, W - 90, 1560), radius=50, fill=t.accent)
        p = price(first)
        if p is not None:
            pen.text((W / 2, 1410), inr(p), 190, (20, 20, 20), anchor="mm")
        was = rd.fact(f"prices.{first}.highest_90d")
        if was is not None and p is not None and was > p:
            s = f"{rd.text['was']} {inr(int(was))}"
            w = pen.text((W / 2, 1620), s, 80, t.accent2, anchor="ma")
            pen.d.line((W / 2 - w / 2, 1672, W / 2 + w / 2, 1672), fill=t.accent2, width=8)

    foot = rd.text["price_as_of"].format(date=long_date(brief.as_of, months))
    fs = 30 if not portrait else 38
    pen.text((40, H - 28), foot, fs, t.text, bold=False, anchor="lm")
    if rd.sample:
        pen.sample_marks("SAMPLE DATA")  # short, so it never collides with the headline
    out.parent.mkdir(parents=True, exist_ok=True)
    pen.img.save(out, optimize=True)
    return out


def _headline(
    pen: _Pen,
    head: str,
    sub: str,
    *,
    left: tuple[int, int, int],
    portrait: bool,
    accent_head: bool = False,
) -> None:
    x, y, max_w = left
    t: Theme = pen.t
    size = pen.fit(head, 130, max_w, 60)
    pen.text((x, y), head, size, t.accent if accent_head else t.text)
    y2 = y + int(size * 1.45)
    pen.text((x, y2), sub, pen.fit(sub, 110, max_w, 60), t.accent if not accent_head else t.text)


def _price_badge(pen: _Pen, price: int | None, xy: tuple[int, int], size: int) -> None:
    if price is None:
        return
    s = inr(price)
    w = pen.f.width(s, size, True)
    x, y = xy
    pen.d.rounded_rectangle(
        (x, y, x + w + size, y + size * 1.7), radius=int(size * 0.85), fill=pen.t.accent
    )
    pen.text((x + size / 2, y + size * 0.85), s, size, (20, 20, 20), anchor="lm")
