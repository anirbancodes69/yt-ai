"""The fact-checker must fail loudly on every kind of wrong claim."""

from __future__ import annotations

import pytest

from channel.factcheck import extract_numbers
from channel.scripts.models import Script
from channel.scripts.pipeline import Context, build_script, fact_check
from channel.scripts.templates import write_from_template
from tests.scripts_helpers import claim, codes, mutate, section

MULT = {"लाख": 100000, "lakh": 100000, "हज़ार": 1000, "thousand": 1000}
TOP = "product-tarang-t9"


@pytest.fixture(scope="module")
def ctx(settings) -> Context:
    return Context.load(settings)


@pytest.fixture(scope="module")
def good(ctx) -> Script:
    brief = ctx.brief("best_under", {"band": 20000}, ctx.settings.demo.as_of, "hi")
    ws = write_from_template(brief, ctx.facts(brief.as_of), ctx.dataset, ctx.scoring)
    return build_script(ctx, brief, ws, "template")


# ---------------------------------------------------------------- number extraction


@pytest.mark.parametrize(
    ("text", "nums"),
    [
        ("कीमत 18,999 रुपये", [18999]),
        ("AnTuTu 7,20,000 और 120Hz", [720000, 120]),
        ("6.67 इंच", [6.67]),
        ("कीमत १८,९९९ रुपये", [18999]),  # Devanagari digits
        ("स्कोर 7.2 लाख", [720000]),
        ("20 हज़ार के अंदर", [20000]),
        ("5,000mAh, 67W", [5000, 67]),
        ("कोई नंबर नहीं", []),
    ],
)
def test_extract_numbers(text, nums) -> None:
    assert extract_numbers(text, MULT) == pytest.approx(nums)


# ---------------------------------------------------------------- the happy path


def test_template_script_passes(ctx, good) -> None:
    report = fact_check(ctx, good)
    assert report.status == "pass", report.summary()
    assert report.claims_checked > 100


def test_english_script_passes(ctx) -> None:
    brief = ctx.brief("best_under", {"band": 20000}, ctx.settings.demo.as_of, "en")
    ws = write_from_template(brief, ctx.facts(brief.as_of), ctx.dataset, ctx.scoring)
    report = fact_check(ctx, build_script(ctx, brief, ws, "template"))
    assert report.status == "pass", report.summary()


# ---------------------------------------------------------------- deliberately wrong claims


def _wrong_battery(d):
    s = section(d, TOP)
    c = claim(s, "battery_mah")
    s["narration"] = s["narration"].replace("5,000mAh", "6,000mAh")
    c["text"], c["value"] = "6,000mAh", 6000


def _wrong_value_right_text(d):
    claim(section(d, TOP), "battery_mah")["value"] = 6000


def _text_disagrees_with_value(d):
    s = section(d, TOP)
    s["narration"] = s["narration"].replace("5,000mAh", "6,000mAh")
    claim(s, "battery_mah")["text"] = "6,000mAh"


def _invented_number(d):
    section(d, TOP)["narration"] += " यह 2 दिन तक चलता है।"


def _invented_number_devanagari(d):
    section(d, TOP)["narration"] += " इसमें ४ स्पीकर हैं।"


def _wrong_lakh(d):
    s = section(d, TOP)
    s["narration"] = s["narration"].replace("7,20,000", "8 लाख")
    claim(s, "antutu_v10")["text"] = "स्कोर 8 लाख"


def _unknown_ref(d):
    claim(section(d, TOP), "battery_mah")["ref"] = "products.tarang-t9.specs.battery_wh"


def _off_brief_product(d):
    s = section(d, TOP)
    s["narration"] += " Zentra Z8 Pro की कीमत 27,999 रुपये है।"
    s["claims"].append(
        {"text": "27,999 रुपये", "ref": "prices.zentra-z8-pro.current", "value": 27999}
    )


def _hands_on(d):
    section(d, TOP)["narration"] += " मैंने इस्तेमाल करके देखा, कैमरा अच्छा है।"


def _ois_negation_flipped(d):
    s = section(d, TOP)
    s["narration"] = s["narration"].replace("OIS मिलता है", "OIS नहीं मिलता है")
    claim(s, "main_camera_ois")["text"] = "OIS नहीं मिलता है"


def _claim_text_missing(d):
    claim(section(d, TOP), "battery_mah")["text"] = "5,000 mAh की बैटरी"


def _disclosure_removed(d):
    d["sections"] = [s for s in d["sections"] if s["id"] != "sys-disclosure"]


def _disclosure_softened(d):
    section(d, "sys-disclosure")["narration"] = "डिस्क्रिप्शन देखें।"


def _sample_notice_removed(d):
    d["sections"] = [s for s in d["sections"] if s["id"] != "sys-sample_notice"]


def _verdict_wrong_winner(d):
    v = section(d, "verdict")
    v["claims"] = [c for c in v["claims"] if "tarang-t9" not in c["ref"]]
    v["narration"] = "तो हमारा फ़ैसला साफ़ है। " + v["narration"].split("। ", 2)[-1]


def _no_verdict(d):
    d["sections"] = [s for s in d["sections"] if s["kind"] != "verdict"]


def _product_after_verdict(d):
    secs = d["sections"]
    top = next(s for s in secs if s["id"] == TOP)
    secs.remove(top)
    secs.append(top)


def _ranked_product_dropped(d):
    d["sections"] = [s for s in d["sections"] if s["id"] != "product-zentra-z5"]


def _heading_number(d):
    section(d, TOP)["heading"] = "नंबर 2: Tarang T9"


def _title_number(d):
    d["title"] = "25,000 रुपये के अंदर बेस्ट स्मार्टफ़ोन"


def _sample_flag_cleared(d):
    d["sample"] = False


def _brief_tampered(d):
    d["brief"]["verdict_product_id"] = "ostra-o2"


def _zero_without_negation(d):
    s = section(d, "product-zentra-z5")
    c = claim(s, "ultrawide_mp")
    s["narration"] = s["narration"].replace(c["text"], "अल्ट्रावाइड कैमरा भी है")
    c["text"] = "अल्ट्रावाइड कैमरा भी है"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (_wrong_battery, "value_mismatch"),
        (_wrong_value_right_text, "value_mismatch"),
        (_text_disagrees_with_value, "claim_number_mismatch"),
        (_invented_number, "unbacked_number"),
        (_invented_number_devanagari, "unbacked_number"),
        (_wrong_lakh, "claim_number_mismatch"),
        (_unknown_ref, "unknown_ref"),
        (_off_brief_product, "off_brief_product"),
        (_hands_on, "banned_phrase"),
        (_ois_negation_flipped, "claim_number_mismatch"),
        (_claim_text_missing, "claim_text_not_found"),
        (_disclosure_removed, "missing_system_section"),
        (_disclosure_softened, "system_section_modified"),
        (_sample_notice_removed, "missing_system_section"),
        (_verdict_wrong_winner, "verdict_missing_winner"),
        (_no_verdict, "structure"),
        (_product_after_verdict, "structure"),
        (_ranked_product_dropped, "structure"),
        (_heading_number, "unbacked_number"),
        (_title_number, "unbacked_number"),
        (_sample_flag_cleared, "sample_flag"),
        (_brief_tampered, "brief_mismatch"),
        (_zero_without_negation, "claim_number_mismatch"),
    ],
)
def test_wrong_claims_fail(ctx, good, mutation, expected) -> None:
    report = fact_check(ctx, mutate(good, mutation))
    assert report.status == "fail"
    assert expected in codes(report), report.summary()


def test_correct_lakh_form_passes(ctx, good) -> None:
    def lakh(d):
        s = section(d, TOP)
        s["narration"] = s["narration"].replace("7,20,000", "7.2 लाख")
        claim(s, "antutu_v10")["text"] = "स्कोर 7.2 लाख"

    report = fact_check(ctx, mutate(good, lakh))
    assert report.status == "pass", report.summary()


def test_stale_fingerprint_fails(ctx, good) -> None:
    report = fact_check(ctx, mutate(good, lambda d: d.__setitem__("data_fingerprint", "old")))
    assert "stale_script" in codes(report)


def test_length_is_a_warning_not_an_error(ctx, good) -> None:
    def pad(d):
        section(d, "outro")["narration"] += " सब्सक्राइब करें।" * 200

    report = fact_check(ctx, mutate(good, pad))
    assert report.status == "pass"
    assert [w.code for w in report.warnings] == ["length"]
