"""Package checker. Any error blocks upload (see ``require_package``).

Rules
-----
* Title: non-empty, within YouTube's limit, "[SAMPLE]" prefix for sample data.
* Description: line 1 is exactly the affiliate disclosure; within the byte limit
  (UTF-8, so Devanagari counts 3 bytes per character); no "<" or ">" (YouTube
  rejects them); carries the "price as of <date>" note, the method note, the
  SAMPLE notice when applicable, and a link for every product; hashtags <= 15.
* Tags: total and per-tag length limits.
* Every number in the title, description and tags is backed by the fact table
  (product names, URLs and chapter timestamps are masked first).
* No phrase that implies hands-on testing.
* Chapters (long-form): start at 0:00, at least 3, ascending, each >= 10 s.
* Thumbnail: exists, right size and aspect, under 2 MB.
"""

from __future__ import annotations

import itertools
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from channel.data.models import Dataset
from channel.factcheck.checker import FactCheckConfig, Issue, extract_numbers, sha256_text
from channel.facts import Facts, FactValue, product_id_of
from channel.fmt import long_date
from channel.packaging.models import Package, PackagingConfig

URL_RE = re.compile(r"https?://\S+")
TS_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
MASK = " ⁣ "
THUMB_SIZE = {"landscape": (1280, 720), "portrait": (1080, 1920)}


class PackageCheckReport(BaseModel):
    video_id: str
    status: Literal["pass", "fail"]
    checked_at: datetime
    package_sha256: str
    errors: list[Issue]
    warnings: list[str]

    def summary(self) -> str:
        lines = [
            f"{self.status.upper()}: package {self.video_id} — {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings"
        ]
        lines += [f"  ERROR {i.code}: {i.detail}" for i in self.errors]
        lines += [f"  warn  {w}" for w in self.warnings]
        return "\n".join(lines)


def package_sha256(pkg: Package, thumbnail: Path) -> str:
    thumb = thumbnail.read_bytes() if thumbnail.exists() else b""
    return sha256_text(pkg.model_dump_json() + sha256_text(thumb.hex()))


def allowed_numbers(
    facts: Facts, dataset: Dataset, pkg: Package, as_of_parts: list[int]
) -> list[float]:
    """Numbers a title/description may contain: fact values for the video's products and
    band, plus the date parts, plus the rank numbers 1..N for N products."""
    ok: set[float] = set(float(x) for x in as_of_parts)
    ok.update(float(i) for i in range(1, len(pkg.links) + 1))
    for ref, fact in facts.items():
        parts = ref.split(".")
        pid = product_id_of(ref)
        if parts[0] == "meta" or (
            pid is not None and pid in {link.product_id for link in pkg.links}
        ):
            v: FactValue = fact.value
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ok.add(float(v))
        elif (
            parts[0] == "ranking"
            and len(parts) == 3
            and parts[2] in ("band", "count")
            and isinstance(fact.value, (int, float))
        ):
            ok.add(float(fact.value))
    return sorted(ok)


def _mask_names(text: str, dataset: Dataset) -> str:
    names = {f"{p.brand} {p.model}" for p in dataset.products.values()}
    names |= {p.model for p in dataset.products.values()}
    names |= {"5G", "4G"}
    for n in sorted((n for n in names if re.search(r"\d", n)), key=len, reverse=True):
        text = re.sub(re.escape(n), MASK, text, flags=re.IGNORECASE)
    return text


def check_package(
    pkg: Package,
    *,
    cfg: PackagingConfig,
    facts: Facts,
    dataset: Dataset,
    factcheck_cfg: FactCheckConfig,
    months: list[str],
    as_of,
    package_dir: Path,
    product_ids: tuple[str, ...],
) -> PackageCheckReport:
    errors: list[Issue] = []
    lim = cfg.limits
    text = cfg.lang(pkg.lang)

    def err(code: str, detail: str) -> None:
        errors.append(Issue(code=code, detail=detail))

    # ---- title
    if not pkg.title.strip():
        err("title_empty", "title is empty")
    if len(pkg.title) > lim.title_chars:
        err("title_too_long", f"{len(pkg.title)} chars, limit {lim.title_chars}")
    if pkg.sample and not pkg.title.startswith(text.title_sample_prefix):
        err("title_sample", "SAMPLE data must be marked in the title")
    if not pkg.sample and text.title_sample_prefix.strip() in pkg.title:
        err("title_sample", "real data must not carry a SAMPLE marker")
    warnings = list(pkg.warnings)
    if len(pkg.title) > lim.title_soft_chars:
        warnings.append(
            f"title is {len(pkg.title)} chars; may be cut off in search "
            f"(soft limit {lim.title_soft_chars})"
        )

    # ---- description
    desc = pkg.description
    first_line = desc.split("\n", 1)[0]
    if first_line != text.disclosure:
        err(
            "disclosure_not_first",
            "line 1 of the description must be exactly the affiliate disclosure",
        )
    size = len(desc.encode("utf-8"))
    if size > lim.description_bytes:
        err("description_too_long", f"{size} bytes, limit {lim.description_bytes}")
    for field_name, value in (
        ("title", pkg.title),
        ("description", desc),
        ("tags", " ".join(pkg.tags)),
    ):
        if "<" in value or ">" in value:
            err("angle_brackets", f"{field_name} contains < or >, which YouTube rejects")
    date_str = long_date(as_of, months)
    if text.price_note.format(date=date_str) not in desc:
        err("missing_price_date", "description lacks the 'price as of' note")
    if pkg.sample and text.sample_notice not in desc:
        err("missing_sample_notice", "SAMPLE data needs the sample notice in the description")
    for link in pkg.links:
        if link.url not in desc:
            err("missing_link", f"link for {link.product_id} ({link.label}) not in the description")
    linked = {link.product_id for link in pkg.links}
    for pid in product_ids:
        if pid not in linked:
            warnings.append(f"{pid}: no retailer link in the description")
    if len(pkg.hashtags) > lim.hashtags_max:
        err(
            "too_many_hashtags",
            f"{len(pkg.hashtags)} hashtags; YouTube ignores all over {lim.hashtags_max}",
        )
    if not any(link.affiliate for link in pkg.links):
        warnings.append(
            "no link carries an affiliate tag; the disclosure says there are affiliate links"
        )

    # ---- tags
    total = sum(len(t) + (2 if " " in t else 0) + 1 for t in pkg.tags)
    if total > lim.tags_chars:
        err("tags_too_long", f"{total} chars, limit {lim.tags_chars}")
    for t in pkg.tags:
        if len(t) > lim.tag_chars:
            err("tag_too_long", f"tag {t!r} is {len(t)} chars, limit {lim.tag_chars}")

    # ---- numbers
    ok = allowed_numbers(facts, dataset, pkg, [as_of.day, as_of.month, as_of.year])
    for where, raw in (("title", pkg.title), ("description", desc), ("tags", " ".join(pkg.tags))):
        scrubbed = TS_RE.sub(MASK, URL_RE.sub(MASK, raw))
        scrubbed = _mask_names(scrubbed, dataset)
        for x in extract_numbers(scrubbed, factcheck_cfg.multipliers):
            if not any(math.isclose(x, a, abs_tol=1e-9) for a in ok):
                err("unbacked_number", f"{where}: number {x:g} is not backed by the data")

    # ---- hands-on phrases
    blob = f"{pkg.title}\n{desc}\n{' '.join(pkg.tags)}".casefold()
    for phrase in factcheck_cfg.banned_phrases.get(pkg.lang, []):
        if phrase.casefold() in blob:
            err("banned_phrase", f"implies hands-on testing: {phrase!r}")

    # ---- chapters
    if pkg.orientation == "landscape" and not pkg.preview:
        ch = pkg.chapters
        if len(ch) < lim.chapter_min_count:
            err("chapters", f"{len(ch)} chapters; YouTube needs at least {lim.chapter_min_count}")
        elif ch[0].start_s != 0:
            err("chapters", "first chapter must start at 0:00")
        else:
            for a, b in itertools.pairwise(ch):
                if b.start_s - a.start_s < lim.chapter_min_seconds:
                    err(
                        "chapters",
                        f"chapter {a.title!r} is shorter than {lim.chapter_min_seconds} s",
                    )
    if pkg.chapters and pkg.orientation == "landscape":
        for c in pkg.chapters:
            if f"{c.title}" not in desc:
                err("chapters", f"chapter {c.title!r} missing from the description")

    # ---- thumbnail
    thumb = package_dir / pkg.thumbnail
    if not thumb.exists():
        err("thumbnail_missing", f"{thumb.name} not found")
    else:
        with Image.open(thumb) as im:
            want = THUMB_SIZE[pkg.orientation]
            if im.size != want:
                err("thumbnail_size", f"{im.size}, expected {want}")
        if thumb.stat().st_size > lim.thumbnail_bytes:
            err("thumbnail_bytes", f"{thumb.stat().st_size} bytes, limit {lim.thumbnail_bytes}")

    return PackageCheckReport(
        video_id=pkg.video_id,
        status="fail" if errors else "pass",
        checked_at=datetime.now(UTC),
        package_sha256=package_sha256(pkg, thumb),
        errors=errors,
        warnings=sorted(set(warnings)),
    )
