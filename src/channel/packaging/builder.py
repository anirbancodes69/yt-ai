"""Build title, description, tags, chapters and affiliate links for one video.

Everything is assembled by code from the brief and the fact table. There is no
LLM call, so packaging costs nothing and its numbers can only come from facts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from channel.data.models import Dataset
from channel.data.pricing import current_price
from channel.facts import Facts
from channel.fmt import inr, long_date
from channel.packaging.models import Chapter, Link, Package, PackagingConfig
from channel.scripts.models import Brief, Script
from channel.voice.narration import SectionSpan


class PackagingError(RuntimeError):
    pass


def month_year(brief: Brief, months: list[str]) -> str:
    return f"{months[brief.as_of.month - 1]} {brief.as_of.year}"


def fmt_ts(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------- links


def affiliate_url(url: str, param: str, value: str) -> str:
    """Add (or replace) one query parameter, keeping everything else intact."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    query.append((param, value))
    return urlunparse(parts._replace(query=urlencode(query)))


def build_links(
    brief: Brief,
    dataset: Dataset,
    cfg: PackagingConfig,
    env: Mapping[str, str],
    *,
    max_age_days: int,
    warnings: list[str],
) -> list[Link]:
    """Links for each product, the retailer with the current price first."""
    links: list[Link] = []
    for pid in brief.product_ids:
        product = dataset.product(pid)
        best = current_price(dataset.prices, pid, as_of=brief.as_of, max_age_days=max_age_days)
        listings = sorted(
            product.listings,
            key=lambda li: (best is None or li.retailer != best.retailer, li.retailer),
        )
        if not listings:
            warnings.append(f"{pid}: no retailer listing; no link in the description")
        for li in listings:
            rcfg = cfg.retailers.get(li.retailer)
            if rcfg is None:
                warnings.append(
                    f"{pid}: retailer {li.retailer!r} not in packaging.yaml; plain link"
                )
                links.append(
                    Link(
                        product_id=pid,
                        retailer=li.retailer,
                        label=li.retailer,
                        url=li.url,
                        affiliate=False,
                    )
                )
                continue
            value = env.get(rcfg.env) or (rcfg.sample_value if product.sample else None)
            if not value:
                warnings.append(f"{rcfg.env} is not set: {rcfg.label} links have no affiliate tag")
                links.append(
                    Link(
                        product_id=pid,
                        retailer=li.retailer,
                        label=rcfg.label,
                        url=li.url,
                        affiliate=False,
                    )
                )
            else:
                links.append(
                    Link(
                        product_id=pid,
                        retailer=li.retailer,
                        label=rcfg.label,
                        url=affiliate_url(li.url, rcfg.param, value),
                        affiliate=True,
                    )
                )
    return links


# ---------------------------------------------------------------------------- chapters


def build_chapters(
    script: Script,
    spans: list[SectionSpan],
    *,
    intro_title: str,
    min_seconds: int,
) -> list[Chapter]:
    """One chapter per content section, from the real narration timeline.

    The opening sections (hook, disclosure, method, sample notice) become one
    "intro" chapter at 0:00. Chapters shorter than ``min_seconds`` (YouTube's
    minimum is 10 s) merge into the previous one. A chapter titled with a section
    heading inherits the fact-checked numbers in that heading.
    """
    sections = {s.id: s for s in script.sections}
    raw: list[tuple[float, str]] = []
    for span in spans:
        sec = sections[span.section_id]
        if sec.kind in ("hook", "disclosure", "method", "sample_notice"):
            title = intro_title
        elif sec.kind == "outro":
            continue  # tail of the verdict chapter
        else:
            title = sec.heading
        if raw and raw[-1][1] == title:
            continue
        raw.append((span.start, title))
    if not raw:
        return []
    raw[0] = (0.0, raw[0][1])
    chapters = [Chapter(start_s=0, title=raw[0][1])]
    for start, title in raw[1:]:
        if int(start) - chapters[-1].start_s < min_seconds:
            continue  # too short: its time stays with the previous chapter
        chapters.append(Chapter(start_s=int(start), title=title))
    return chapters


# ---------------------------------------------------------------------------- text


def _fields(
    brief: Brief,
    facts: Facts,
    dataset: Dataset,
    months: list[str],
    text,
) -> dict[str, str]:
    def v(ref: str):
        f = facts.get(ref)
        return None if f is None else f.value

    def name(pid: str) -> str:
        p = dataset.product(pid)
        return f"{p.brand} {p.model}"

    band = brief.band_inr
    fields: dict[str, str] = {"month_year": month_year(brief, months)}
    if band is not None:
        fields["band"] = inr(band)
        fields["band_plain"] = str(band)
        fields["count"] = str(v(f"ranking.{band}.count"))
    first = brief.product_ids[0]
    fields["product"] = name(first)
    price = v(f"prices.{first}.current")
    if price is not None:
        fields["price"] = inr(price)
    winner = brief.verdict_product_id
    if winner is not None:
        fields["winner"] = name(winner)
        wp = v(f"prices.{winner}.current")
        if wp is not None:
            fields["winner_price"] = inr(wp)
    fields["verdict"] = name(winner) if winner is not None else text.verdict_tie
    if len(brief.product_ids) >= 2:
        fields["a"], fields["b"] = name(brief.product_ids[0]), name(brief.product_ids[1])
    drop, high = v(f"prices.{first}.drop_from_high_90d"), v(f"prices.{first}.highest_90d")
    if drop is not None and high is not None:
        fields["drop"], fields["high"] = inr(drop), inr(high)
    return fields


def _fill(template: str, fields: dict[str, str], where: str) -> str:
    try:
        return template.format(**fields)
    except KeyError as exc:
        raise PackagingError(f"{where}: needs {exc} which this video does not have") from exc


def build_package(
    *,
    brief: Brief,
    facts: Facts,
    dataset: Dataset,
    cfg: PackagingConfig,
    months: list[str],
    method_note: str,
    env: Mapping[str, str] | None = None,
    max_price_age_days: int,
    video_id: str | None = None,
    script: Script | None = None,
    spans: list[SectionSpan] | None = None,
    variant: int = 0,
) -> Package:
    env = os.environ if env is None else env
    text = cfg.lang(brief.lang)
    sample = dataset.is_sample
    fields = _fields(brief, facts, dataset, months, text)
    warnings: list[str] = []

    prefix = text.title_sample_prefix if sample else ""
    templates = text.titles[brief.format]
    variants = [prefix + _fill(t, fields, f"title[{brief.format}]") for t in templates]
    title = variants[variant % len(variants)]

    date_str = long_date(brief.as_of, months)
    links = build_links(
        brief, dataset, cfg, env, max_age_days=max_price_age_days, warnings=warnings
    )

    chapters: list[Chapter] = []
    if brief.orientation == "landscape" and script is not None and spans:
        chapters = build_chapters(
            script,
            spans,
            intro_title=text.chapter_intro,
            min_seconds=cfg.limits.chapter_min_seconds,
        )

    hashtags = list(text.hashtags[brief.format])
    lines = [text.disclosure, ""]
    if sample:
        lines += [text.sample_notice, ""]
    lines += [_fill(text.summaries[brief.format], fields, f"summary[{brief.format}]"), method_note]
    price_note = text.price_note.format(date=date_str)
    if price_note not in method_note:  # the long method note already says it; the Short's doesn't
        lines.append(price_note)
    lines.append("")

    if links:
        lines.append(text.links_heading)
        shown: set[str] = set()
        for link in links:
            p = dataset.product(link.product_id)
            price = facts.get(f"prices.{link.product_id}.current")
            first_for_product = link.product_id not in shown
            shown.add(link.product_id)
            if price is not None and first_for_product:
                lines.append(
                    text.link_line.format(
                        name=f"{p.brand} {p.model}",
                        price=inr(int(price.value)),
                        price_as_of=text.price_as_of.format(date=date_str),
                        retailer=link.label,
                        url=link.url,
                    )
                )
            else:
                lines.append(
                    text.link_line_no_price.format(
                        name=f"{p.brand} {p.model}", retailer=link.label, url=link.url
                    )
                )
        lines.append("")
    if chapters:
        lines.append(text.chapters_heading)
        lines += [f"{fmt_ts(c.start_s)} {c.title}" for c in chapters]
        lines.append("")
    lines.append(" ".join(hashtags))
    description = "\n".join(lines).strip() + "\n"

    tags: list[str] = []
    for group in ("common", brief.format):
        for t in text.tags.get(group, []):
            filled = _fill(t, fields, f"tags[{group}]")
            if filled not in tags:
                tags.append(filled)
    for pid in brief.product_ids:
        p = dataset.product(pid)
        for t in (f"{p.brand} {p.model}", p.brand):
            if t not in tags:
                tags.append(t)
    kept: list[str] = []
    total = 0
    for t in tags:
        cost = len(t) + (2 if " " in t else 0) + 1  # quotes around multi-word tags + comma
        if len(t) <= cfg.limits.tag_chars and total + cost <= cfg.limits.tags_chars:
            kept.append(t)
            total += cost

    return Package(
        video_id=video_id or brief.video_id,
        format=brief.format,
        lang=brief.lang,
        sample=sample,
        orientation=brief.orientation,
        preview=script is None,
        title=title,
        title_variants=variants,
        description=description,
        tags=kept,
        hashtags=hashtags,
        chapters=chapters,
        links=links,
        category_id=cfg.category_id,
        default_language=brief.lang,
        made_for_kids=cfg.made_for_kids,
        contains_synthetic_media=cfg.contains_synthetic_media,
        thumbnail="thumbnail.png",
        warnings=sorted(set(warnings)),
    )
