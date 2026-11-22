from __future__ import annotations

import hashlib
import itertools
import json
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from channel.factcheck import load_factcheck_config
from channel.packaging import (
    PackageBlocked,
    affiliate_url,
    check_package,
    load_packaging,
    package_video,
    preview_all,
    preview_package,
    require_package,
)
from channel.packaging.builder import build_chapters, build_links, fmt_ts
from channel.packaging.models import Chapter, Package
from channel.render.pipeline import CONTACT_PARAMS
from channel.scripts.models import Script
from channel.scripts.pipeline import Context, generate
from channel.voice.narration import SectionSpan

AS_OF = date(2026, 11, 20)


@pytest.fixture(scope="module")
def ctx(settings) -> Context:
    return Context.load(settings)


@pytest.fixture(scope="module")
def cfg(settings):
    return load_packaging(settings.paths.packaging)


def _check(ctx, cfg, pkg: Package, pkg_dir: Path, brief):
    return check_package(
        pkg,
        cfg=cfg,
        facts=ctx.facts(brief.as_of),
        dataset=ctx.dataset,
        factcheck_cfg=load_factcheck_config(ctx.settings.paths.factcheck),
        months=ctx.script_text[brief.lang]["months"],
        as_of=brief.as_of,
        package_dir=pkg_dir,
        product_ids=brief.product_ids,
    )


def _codes(report) -> list[str]:
    return [e.code for e in report.errors]


# ---------------------------------------------------------------- affiliate links


def test_affiliate_url_adds_and_replaces_param() -> None:
    assert affiliate_url("https://x.in/p/1", "tag", "me-21") == "https://x.in/p/1?tag=me-21"
    assert affiliate_url("https://x.in/p/1?a=1&tag=old", "tag", "me-21") == (
        "https://x.in/p/1?a=1&tag=me-21"
    )


def test_links_use_env_tag_and_current_price_retailer_first(ctx, cfg) -> None:
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, AS_OF, "hi")
    warnings: list[str] = []
    links = build_links(
        brief,
        ctx.dataset,
        cfg,
        {"SAMPLE_AFFILIATE_TAG": "mytag"},
        max_age_days=7,
        warnings=warnings,
    )
    assert [lk.retailer for lk in links] == ["sample-store-a", "sample-store-b"]  # A is cheaper
    assert all(lk.affiliate and lk.url.endswith("ref=mytag") for lk in links)
    assert warnings == []


def test_sample_value_only_for_sample_data_and_missing_env_warns(ctx, cfg) -> None:
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, AS_OF, "hi")
    no_fallback = cfg.model_copy(deep=True)
    for r in no_fallback.retailers.values():
        r.sample_value = None
    warnings: list[str] = []
    links = build_links(brief, ctx.dataset, no_fallback, {}, max_age_days=7, warnings=warnings)
    assert not any(lk.affiliate for lk in links)
    assert links[0].url == "https://example.com/sample/sample-store-a/ostra-o2"
    assert any("SAMPLE_AFFILIATE_TAG is not set" in w for w in warnings)


# ---------------------------------------------------------------- chapters


@pytest.mark.parametrize(
    ("s", "ts"), [(0, "0:00"), (65, "1:05"), (3599, "59:59"), (3600, "1:00:00")]
)
def test_fmt_ts(s, ts) -> None:
    assert fmt_ts(s) == ts


def test_chapters_merge_short_sections_and_start_at_zero(ctx, tmp_settings) -> None:
    c2 = Context.load(tmp_settings)
    brief = c2.brief("best_under", {"band": 20000}, AS_OF, "hi")
    script, _r, _d = generate(c2, brief, writer="template")
    spans, t = [], 0.4
    for sec in script.sections:
        dur = 4.0 if sec.kind in ("sys-sample",) else 30.0
        spans.append(SectionSpan(sec.id, sec.kind, t, t + dur))
        t += dur + 0.5
    chapters = build_chapters(script, spans, intro_title="परिचय", min_seconds=10)
    assert chapters[0] == Chapter(start_s=0, title="परिचय")
    assert [c.title for c in chapters][-1] == "फ़ैसला"  # outro merged into the verdict
    assert all(b.start_s - a.start_s >= 10 for a, b in itertools.pairwise(chapters))
    assert [c.title for c in chapters].count("परिचय") == 1


# ---------------------------------------------------------------- every format


@pytest.mark.parametrize("lang", ["hi", "en"])
def test_all_five_formats_package_and_pass(ctx, tmp_path, lang) -> None:
    results = preview_all(ctx, tmp_path, lang=lang, as_of=AS_OF)
    assert {p.format for p, _ in results} == set(CONTACT_PARAMS)
    for pkg, report in results:
        assert report.status == "pass", report.summary()
        assert pkg.title.startswith("[SAMPLE] ") and len(pkg.title) <= 100
        assert pkg.preview and pkg.default_language == lang
        first = pkg.description.split("\n", 1)[0]
        assert first == load_packaging(ctx.settings.paths.packaging).lang(lang).disclosure
        assert "<" not in pkg.description and ">" not in pkg.description
        assert len(pkg.hashtags) <= 15


def test_titles_are_distinct_per_format_and_variants_differ(ctx, tmp_path) -> None:
    results = preview_all(ctx, tmp_path, lang="hi", as_of=AS_OF)
    assert len({p.title for p, _ in results}) == 5
    for pkg, _ in results:
        assert len(set(pkg.title_variants)) == len(pkg.title_variants) >= 2
    brief = ctx.brief("best_under", {"band": 20000}, AS_OF, "hi")
    v1, _ = preview_package(ctx, brief, tmp_path / "v1", variant=1)
    assert v1.title == v1.title_variants[1] != v1.title_variants[0]


def test_description_bytes_not_chars(ctx, tmp_path) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, AS_OF, "hi")
    pkg, _ = preview_package(ctx, brief, tmp_path)
    assert len(pkg.description.encode()) > len(pkg.description) * 1.5  # Devanagari is 3 bytes


def test_short_has_hashtag_and_no_chapters(ctx, tmp_path) -> None:
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, AS_OF, "hi")
    pkg, report = preview_package(ctx, brief, tmp_path)
    assert "#Shorts" in pkg.hashtags and pkg.chapters == [] and report.status == "pass"
    assert "बनाम" not in pkg.title and "₹19,999" in pkg.title


def test_price_as_of_date_in_description(ctx, tmp_path) -> None:
    for fmt in ("best_under", "deal_short"):
        brief = ctx.brief(fmt, CONTACT_PARAMS[fmt], AS_OF, "hi")
        pkg, _ = preview_package(ctx, brief, tmp_path / fmt)
        assert "20 नवंबर 2026" in pkg.description


# ---------------------------------------------------------------- the checker catches problems


@pytest.fixture
def good(ctx, tmp_path):
    brief = ctx.brief("best_under", {"band": 20000}, AS_OF, "hi")
    pkg, report = preview_package(ctx, brief, tmp_path)
    assert report.status == "pass"
    return pkg, tmp_path, brief


def _mut(pkg: Package, **kw) -> Package:
    return pkg.model_copy(update=kw)


def test_disclosure_must_be_first(ctx, cfg, good) -> None:
    pkg, d, brief = good
    lines = pkg.description.split("\n")
    swapped = "\n".join(["", *lines])
    assert "disclosure_not_first" in _codes(
        _check(ctx, cfg, _mut(pkg, description=swapped), d, brief)
    )
    no_disc = "\n".join(lines[2:])
    assert "disclosure_not_first" in _codes(
        _check(ctx, cfg, _mut(pkg, description=no_disc), d, brief)
    )


@pytest.mark.parametrize(
    ("field", "value_fn", "code"),
    [
        ("description", lambda p: p.description + "x" * 6000, "description_too_long"),
        ("title", lambda p: p.title + "अ" * 100, "title_too_long"),
        ("title", lambda p: p.title.replace("[SAMPLE] ", ""), "title_sample"),
        ("title", lambda p: p.title + " <b>", "angle_brackets"),
        ("description", lambda p: p.description + "\n5 > 3", "angle_brackets"),
        ("title", lambda p: p.title + " 25,000 रुपये", "unbacked_number"),
        ("description", lambda p: p.description + "\nबैटरी 8,888mAh", "unbacked_number"),
        ("tags", lambda p: [*p.tags, "best phone 77777"], "unbacked_number"),
        ("title", lambda p: p.title + " (हमने टेस्ट किया)", "banned_phrase"),
        (
            "description",
            lambda p: p.description.replace("20 नवंबर 2026", "21 नवंबर 2026"),
            "missing_price_date",
        ),
        ("description", lambda p: p.description.replace("सैंपल डेटा", "डेटा"), "missing_sample_notice"),
        ("tags", lambda p: [*p.tags, "x" * 31], "tag_too_long"),
        ("tags", lambda p: [f"tag number {i:03d}" for i in range(60)], "tags_too_long"),
        ("hashtags", lambda p: [f"#t{i}" for i in range(16)], "too_many_hashtags"),
    ],
)
def test_broken_packages_fail(ctx, cfg, good, field, value_fn, code) -> None:
    pkg, d, brief = good
    report = _check(ctx, cfg, _mut(pkg, **{field: value_fn(pkg)}), d, brief)
    assert report.status == "fail" and code in _codes(report), report.summary()


def test_known_limit_number_pool_is_not_context_bound(ctx, cfg, good) -> None:
    """Description numbers are checked against the pool of this video's fact values, not
    against the sentence they appear in (unlike script claims). Text is code-built from
    templates and hash-gated, so this is a backstop; documented in docs/PACKAGING.md."""
    pkg, d, brief = good
    desc = pkg.description + "\nबैटरी 9,999mAh"  # 9,999 is Kovix K10's price: in the pool
    assert _check(ctx, cfg, _mut(pkg, description=desc), d, brief).status == "pass"


def test_missing_link_is_an_error(ctx, cfg, good) -> None:
    pkg, d, brief = good
    link = pkg.links[0]
    desc = pkg.description.replace(link.url, "")
    assert "missing_link" in _codes(_check(ctx, cfg, _mut(pkg, description=desc), d, brief))


def test_names_urls_and_timestamps_are_not_numbers(ctx, cfg, good) -> None:
    pkg, d, brief = good
    # "Z8", "K10 5G" are product names; URLs contain digits; timestamps are not claims.
    desc = pkg.description + "\nZentra Z8 Pro, Kovix K10 5G https://example.com/a/1234 0:48 12:34\n"
    assert _check(ctx, cfg, _mut(pkg, description=desc), d, brief).status == "pass"


def test_thumbnail_checks(ctx, cfg, good, tmp_path) -> None:
    pkg, d, brief = good
    thumb = d / pkg.thumbnail
    original = thumb.read_bytes()
    Image.new("RGB", (640, 360)).save(thumb)
    assert "thumbnail_size" in _codes(_check(ctx, cfg, pkg, d, brief))
    thumb.unlink()
    assert "thumbnail_missing" in _codes(_check(ctx, cfg, pkg, d, brief))
    thumb.write_bytes(original)
    small = cfg.model_copy(deep=True)
    small.limits.thumbnail_bytes = 1000
    assert "thumbnail_bytes" in _codes(_check(ctx, small, pkg, d, brief))


def test_long_form_chapter_rules(ctx, cfg, tmp_path) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, AS_OF, "hi")
    pkg, _ = preview_package(ctx, brief, tmp_path)
    live = _mut(pkg, preview=False)  # enforce chapter rules as for a real upload
    assert "chapters" in _codes(_check(ctx, cfg, live, tmp_path, brief))  # none
    three = [
        Chapter(start_s=0, title="परिचय"),
        Chapter(start_s=60, title="फ़ैसला"),
        Chapter(start_s=120, title="x"),
    ]
    body = "\n".join(f"{fmt_ts(c.start_s)} {c.title}" for c in three)
    ok = _mut(live, chapters=three, description=pkg.description + body)
    assert _check(ctx, cfg, ok, tmp_path, brief).status == "pass"
    late = _mut(ok, chapters=[Chapter(start_s=5, title="परिचय"), *three[1:]])
    assert any("0:00" in e.detail for e in _check(ctx, cfg, late, tmp_path, brief).errors)
    short = _mut(ok, chapters=[three[0], Chapter(start_s=5, title="फ़ैसला"), three[2]])
    assert any("shorter" in e.detail for e in _check(ctx, cfg, short, tmp_path, brief).errors)


# ---------------------------------------------------------------- thumbnails


def test_thumbnails_sizes_and_distinct(ctx, tmp_path) -> None:
    results = preview_all(ctx, tmp_path, lang="hi", as_of=AS_OF)
    hashes = set()
    for pkg, _ in results:
        path = tmp_path / pkg.format / pkg.thumbnail
        with Image.open(path) as im:
            assert im.size == ((1080, 1920) if pkg.orientation == "portrait" else (1280, 720))
        assert path.stat().st_size < 2_000_000
        hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
    assert len(hashes) == 5


# ---------------------------------------------------------------- the upload gate


@pytest.fixture
def short_video(tmp_settings):
    c = Context.load(tmp_settings)
    brief = c.brief("deal_short", {"product": "ostra-o2"}, AS_OF, "hi")
    _s, _r, d = generate(c, brief, writer="template")
    return c, d


def test_gate_accepts_a_checked_package(short_video) -> None:
    c, d = short_video
    pkg, report, pkg_dir = package_video(c, d)
    assert report.status == "pass" and not pkg.preview and pkg.script_sha256
    assert require_package(d, c.settings.data_files()).video_id == d.name
    assert (pkg_dir / "description.txt").read_text() == pkg.description


@pytest.mark.parametrize("tamper", ["description", "thumbnail", "script", "report", "delete"])
def test_gate_blocks_changes(short_video, tamper) -> None:
    c, d = short_video
    package_video(c, d)
    pkg_dir = d / "package"
    if tamper == "description":
        data = json.loads((pkg_dir / "package.json").read_text())
        data["description"] = "free phones!\n" + data["description"]
        (pkg_dir / "package.json").write_text(json.dumps(data, ensure_ascii=False))
    elif tamper == "thumbnail":
        Image.new("RGB", (1080, 1920), (255, 0, 0)).save(pkg_dir / "thumbnail.png")
    elif tamper == "script":
        data = json.loads((d / "script.json").read_text())
        data["sections"][-1]["narration"] += " ठीक है।"
        (d / "script.json").write_text(json.dumps(data, ensure_ascii=False))
    elif tamper == "report":
        rep = json.loads((pkg_dir / "package_check.json").read_text())
        rep["status"] = "fail"
        (pkg_dir / "package_check.json").write_text(json.dumps(rep))
    else:
        (pkg_dir / "package_check.json").unlink()
    with pytest.raises(PackageBlocked):
        require_package(d, c.settings.data_files())


def test_gate_requires_video_for_long_form_and_rejects_previews(tmp_settings) -> None:
    c = Context.load(tmp_settings)
    brief = c.brief("best_under", {"band": 20000}, AS_OF, "hi")
    script, _r, d = generate(c, brief, writer="template")
    spans, t = [], 0.4
    for sec in script.sections:
        spans.append({"section_id": sec.id, "kind": sec.kind, "start": t, "end": t + 40.0})
        t += 40.5
    (d / "timeline.json").write_text(
        json.dumps({"duration": t, "sections": spans, "sentences": []})
    )
    pkg, report, _ = package_video(c, d)
    assert report.status == "pass", report.summary()
    assert len(pkg.chapters) >= 3 and pkg.chapters[0].start_s == 0
    with pytest.raises(PackageBlocked, match=r"video\.mp4"):
        require_package(d, c.settings.data_files())
    (d / "video.mp4").write_bytes(b"x")
    assert require_package(d, c.settings.data_files())
    preview_package(c, brief, d / "package")  # overwrite with a preview
    with pytest.raises(PackageBlocked, match="preview"):
        require_package(d, c.settings.data_files())


def test_package_refuses_unchecked_script(short_video) -> None:
    from channel.factcheck import FactCheckBlocked

    c, d = short_video
    data = json.loads((d / "script.json").read_text())
    data["title"] = "changed"
    (d / "script.json").write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(FactCheckBlocked):
        package_video(c, d)


def test_script_model_roundtrip_has_sample_flag(short_video) -> None:
    _c, d = short_video
    assert Script.model_validate_json((d / "script.json").read_text()).sample


def test_cli_package_commands(tmp_settings, monkeypatch, capsys) -> None:
    from channel import cli

    monkeypatch.setattr(cli, "load_settings", lambda: tmp_settings)
    assert cli.main(["package-preview"]) == 0
    assert "PASS best_under" in capsys.readouterr().out
    assert cli.main(["script", "deal_short", "--product", "ostra-o2", "--writer", "template"]) == 0
    assert cli.main(["package", "deal_short-ostra-o2-2026-11-20-hi"]) == 0
    assert "upload" not in capsys.readouterr().err
