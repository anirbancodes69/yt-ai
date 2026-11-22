from __future__ import annotations

import pytest

from channel.facts import Fact
from channel.scripts.briefs import BriefError
from channel.scripts.pipeline import Context
from channel.scripts.templates import TemplateUnsupported, write_from_template
from channel.scripts.text import assemble, system_sections


@pytest.fixture(scope="module")
def ctx(settings) -> Context:
    return Context.load(settings)


def _brief(ctx, fmt, params, lang="hi"):
    return ctx.brief(fmt, params, ctx.settings.demo.as_of, lang)


def test_best_under_brief(ctx) -> None:
    b = _brief(ctx, "best_under", {"band": 20000})
    assert b.product_ids[0] == "tarang-t9" and b.verdict_product_id == "tarang-t9"
    assert len(b.product_ids) == 6
    assert b.video_id == "best_under-20000-2026-11-20-hi"
    assert b.orientation == "landscape"


def test_band_with_too_few_products(ctx) -> None:
    with pytest.raises(BriefError, match="only 2 products"):
        _brief(ctx, "best_under", {"band": 10000})


def test_unknown_band(ctx) -> None:
    with pytest.raises(BriefError, match="not in"):
        _brief(ctx, "best_under", {"band": 12345})


def test_head_to_head_brief(ctx) -> None:
    b = _brief(ctx, "head_to_head", {"a": "tarang-t9", "b": "ostra-o2"})
    assert b.product_ids == ("tarang-t9", "ostra-o2")
    assert b.verdict_product_id == "tarang-t9"
    assert b.band_inr is None


def test_launch_brief_includes_rivals(ctx) -> None:
    b = _brief(ctx, "launch_explainer", {"product": "zentra-z8-pro"})
    assert b.product_ids[0] == "zentra-z8-pro" and b.band_inr == 30000
    assert len(b.product_ids) == 3 and "zentra-z8-pro" not in b.product_ids[1:]


def test_deal_short_brief(ctx) -> None:
    b = _brief(ctx, "deal_short", {"product": "ostra-o2"})
    assert b.orientation == "portrait" and b.band_inr == 20000


def test_monthly_refresh_brief(ctx) -> None:
    b = _brief(ctx, "monthly_refresh", {"band": 30000})
    assert b.verdict_product_id == "zentra-z8-pro"


def test_unknown_product(ctx) -> None:
    with pytest.raises(KeyError, match="unknown product"):
        _brief(ctx, "deal_short", {"product": "nope"})


def test_deal_short_needs_a_drop(ctx) -> None:
    b = _brief(ctx, "deal_short", {"product": "ostra-o2"})
    facts = dict(ctx.facts(b.as_of))
    facts["prices.ostra-o2.drop_from_high_90d"] = Fact(0, "test")
    with pytest.raises(TemplateUnsupported, match="no price drop"):
        write_from_template(b, facts, ctx.dataset, ctx.scoring)


def test_template_unsupported_formats(ctx) -> None:
    b = _brief(ctx, "head_to_head", {"a": "tarang-t9", "b": "ostra-o2"})
    with pytest.raises(TemplateUnsupported, match="--writer llm"):
        write_from_template(b, ctx.facts(b.as_of), ctx.dataset, ctx.scoring)


def test_every_template_claim_text_is_in_narration(ctx) -> None:
    for lang in ("hi", "en"):
        b = _brief(ctx, "best_under", {"band": 20000}, lang)
        ws = write_from_template(b, ctx.facts(b.as_of), ctx.dataset, ctx.scoring)
        for s in ws.sections:
            for c in s.claims:
                assert c.text in s.narration


def test_system_sections_long_and_short(ctx) -> None:
    long_ = system_sections(
        _brief(ctx, "best_under", {"band": 20000}), sample=True, text=ctx.script_text
    )
    short = system_sections(
        _brief(ctx, "deal_short", {"product": "ostra-o2"}), sample=True, text=ctx.script_text
    )
    assert [s.kind for s in long_] == ["disclosure", "method", "sample_notice"]
    assert "20 नवंबर 2026" in long_[1].narration
    assert len(short[0].narration) < len(long_[0].narration)
    real = system_sections(
        _brief(ctx, "best_under", {"band": 20000}), sample=False, text=ctx.script_text
    )
    assert [s.kind for s in real] == ["disclosure", "method"]


def test_disclosure_comes_right_after_hook(ctx) -> None:
    b = _brief(ctx, "best_under", {"band": 20000})
    ws = write_from_template(b, ctx.facts(b.as_of), ctx.dataset, ctx.scoring)
    sections = assemble(ws.sections, system_sections(b, sample=True, text=ctx.script_text))
    assert [s.kind for s in sections[:3]] == ["hook", "disclosure", "method"]
    assert sections[-1].kind == "outro" and sections[-2].kind == "verdict"
