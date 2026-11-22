"""Deterministic template writer (no API key, no cost).

Used by `make demo`, by tests, and as a fallback when the LLM budget is spent.
Implements best_under and deal_short; other formats need the LLM writer.

Templates use ``{name}`` placeholders. Each placeholder is either plain text or
a ``C`` (claim): the claim's display text is inserted into the narration and
recorded as a Claim with its fact reference, so every number is cited by
construction.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from channel.data.models import Dataset
from channel.facts import Facts, FactValue
from channel.fmt import num
from channel.scoring import ScoringConfig, score_product
from channel.scripts.models import Brief, Claim, WriterScript, WriterSection


class TemplateUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class C:
    display: str
    ref: str
    value: FactValue


def render(template: str, **parts: str | C) -> tuple[str, list[Claim]]:
    out: list[str] = []
    claims: list[Claim] = []
    for literal, field, _spec, _conv in string.Formatter().parse(template):
        out.append(literal)
        if field is None:
            continue
        part = parts[field]
        if isinstance(part, C):
            out.append(part.display)
            claims.append(Claim(text=part.display, ref=part.ref, value=part.value))
        else:
            out.append(part)
    return "".join(out), claims


# ------------------------------------------------------------------------- phrasebook

UNITS = {
    "hi": {
        "rupees": "{} रुपये",
        "inch": "{} इंच",
        "mp": "{} मेगापिक्सल",
        "years": "{} साल",
        "grams": "{} ग्राम",
        "nits": "{} निट्स",
        "points": "{} अंक",
        "rank": "नंबर {}",
        "phones": "{} फ़ोन्स",
        "scale": "10 में से",
        "days": "पिछले {} दिनों",
        "per": "हर {} रुपये",
        "score": "स्कोर {}",
    },
    "en": {
        "rupees": "₹{}",
        "inch": "{} inches",
        "mp": "{} megapixel",
        "years": "{} years",
        "grams": "{} grams",
        "nits": "{} nits",
        "points": "{} points",
        "rank": "number {}",
        "phones": "{} phones",
        "scale": "out of 10",
        "days": "last {} days",
        "per": "every ₹{}",
        "score": "score of {}",
    },
}

T = {
    "hi": {
        "bu_title": "{band} के अंदर बेस्ट स्मार्टफ़ोन",
        "bu_hook": (
            "क्या आप {band} के अंदर नया स्मार्टफ़ोन ढूँढ रहे हैं? इस वीडियो में हमने {count} को "
            "उनके स्पेसिफिकेशन और मौजूदा कीमत के आधार पर रैंक किया है। आख़िर में आपको साफ़ फ़ैसला "
            "मिलेगा कि इस बजट में कौन-सा फ़ोन लेना सबसे समझदारी है।"
        ),
        "bu_hook_top": (
            "क्या आप {band} के अंदर नया स्मार्टफ़ोन ढूँढ रहे हैं? इस वीडियो में हमने टॉप फ़ोन्स को "
            "उनके स्पेसिफिकेशन और मौजूदा कीमत के आधार पर रैंक किया है। आख़िर में आपको साफ़ फ़ैसला "
            "मिलेगा कि इस बजट में कौन-सा फ़ोन लेना सबसे समझदारी है।"
        ),
        "bu_intro_heading": "स्कोर कैसे बनता है",
        "bu_intro": (
            "हर फ़ोन को {scale} स्कोर दिया गया है। यह स्कोर परफॉर्मेंस, डिस्प्ले, कैमरा, बैटरी, "
            "सॉफ्टवेयर अपडेट और बिल्ड, इन कैटेगरी से मिलकर बनता है। हर कैटेगरी का वज़न पहले से "
            "तय है, इसलिए हर फ़ोन को एक ही पैमाने पर परखा गया है। चलिए, लिस्ट में नीचे से शुरू "
            "करते हैं।"
        ),
        "p_heading": "नंबर {rank}: {name}",
        "p_open": "{rank} पर है {name}, जिसकी मौजूदा कीमत {price} है। इसका ओवरऑल {overall} है। ",
        "p_perf": (
            "परफॉर्मेंस के लिए इसमें {chipset} प्रोसेसर है, जिसका AnTuTu v10 {antutu} बताया गया "
            "है, और साथ में {ram} रैम मिलती है। "
        ),
        "p_display": (
            "डिस्प्ले {size} का {dtype} पैनल है, जिसमें {hz} रिफ्रेश रेट और {nits} तक की पीक "
            "ब्राइटनेस मिलती है। "
        ),
        "p_cam_ois": "कैमरा सेटअप में {mp} का मेन सेंसर है, और इसमें {ois}। ",
        "p_cam_no_ois": "कैमरा सेटअप में {mp} का मेन सेंसर है, लेकिन इसमें {ois}। ",
        "ois_yes": "OIS मिलता है",
        "ois_no": "OIS नहीं है",
        "p_uw": "साथ में {uw} का अल्ट्रावाइड कैमरा भी है। ",
        "p_no_uw": "{uw}। ",
        "no_uw": "अल्ट्रावाइड कैमरा नहीं दिया गया है",
        "p_battery": "बैटरी {mah} की है, और यह {w} चार्जिंग सपोर्ट करता है। ",
        "p_software": "कंपनी ने {os} के OS अपडेट और {sec} के सिक्योरिटी अपडेट का वादा किया है। ",
        "p_build_ip": "इसमें {ip} रेटिंग है, और वज़न {g} है। ",
        "p_build_no_ip": "इसमें {ip} है, और वज़न {g} है। ",
        "no_ip": "कोई IP रेटिंग नहीं",
        "p_hist": "{days} में इसकी सबसे कम कीमत {low} और सबसे ज़्यादा {high} रही है। ",
        "p_hist_flat": "{days} में इसकी कीमत {low} पर स्थिर रही है। ",
        "p_strength": "इसकी सबसे बड़ी ताकत है {label}, जहाँ इसे {score} मिले हैं। ",
        "p_no_strength": "इसकी कोई भी कैटेगरी ख़ास तौर पर बाकी से आगे नहीं दिखती। ",
        "p_weak": "कमज़ोर पक्ष है {label}, जहाँ स्कोर सिर्फ़ {score} है। ",
        "p_buy": "इसे लें अगर आपको {what} चाहिए। ",
        "p_skip": "इसे छोड़ें अगर {what} आपके लिए सबसे ज़रूरी है।",
        "v_heading": "फ़ैसला",
        "bu_verdict": (
            "तो हमारा फ़ैसला साफ़ है। {band} के अंदर {rank} है {name}, जिसका ओवरऑल {overall} "
            "है और कीमत {price} है। "
        ),
        "bu_value": (
            "अगर आपका बजट थोड़ा कम है, तो {name} देखिए, जो {per} पर {spk} देता है, और यह इस "
            "लिस्ट में सबसे ज़्यादा है। "
        ),
        "v_close": "याद रखें, कीमतें बदलती रहती हैं, इसलिए खरीदने से पहले मौजूदा कीमत ज़रूर चेक करें।",
        "outro_heading": "आगे क्या",
        "outro": (
            "सभी फ़ोन्स के लिंक डिस्क्रिप्शन में हैं। ऐसे ही डेटा पर आधारित बायिंग गाइड्स के लिए "
            "चैनल को सब्सक्राइब करें।"
        ),
        "ds_title": "{name} की कीमत गिरी: अब {price}",
        "ds_hook_heading": "प्राइस ड्रॉप",
        "ds_hook": "{name} की कीमत गिरी है!",
        "ds_price_heading": "कीमत",
        "ds_price": "{days} में इसकी कीमत {high} तक थी, और अब यह {price} में मिल रहा है, यानी {drop} कम। ",
        "ds_specs_heading": "ख़ास बातें",
        "ds_specs": "इसमें {dtype} डिस्प्ले, {hz} रिफ्रेश रेट और {mah} बैटरी है। ",
        "ds_verdict_good": "फ़ैसला: {band} के अंदर यह हमारी लिस्ट में {rank} पर है, इसलिए इस कीमत पर यह अच्छी डील है।",
        "ds_verdict_meh": "फ़ैसला: {band} के अंदर यह हमारी लिस्ट में {rank} पर है, इसलिए पहले बाकी ऑप्शन भी देखें।",
        "buy": {
            "performance": "तेज़ परफॉर्मेंस और गेमिंग",
            "display": "बढ़िया डिस्प्ले",
            "camera": "भरोसेमंद कैमरा",
            "battery": "लंबी बैटरी और तेज़ चार्जिंग",
            "software": "लंबे समय तक अपडेट",
            "build": "मज़बूत और हल्का बिल्ड",
        },
        "skip": {
            "performance": "तेज़ परफॉर्मेंस",
            "display": "अच्छा डिस्प्ले",
            "camera": "कैमरा",
            "battery": "बैटरी लाइफ़",
            "software": "लंबे सॉफ्टवेयर अपडेट",
            "build": "वॉटर रेज़िस्टेंस और हल्का वज़न",
        },
    },
    "en": {
        "bu_title": "Best smartphone under {band}",
        "bu_hook": (
            "Looking for a new smartphone under {band}? In this video we rank {count} by their "
            "specifications and current price, and at the end you get a clear verdict on which "
            "one makes the most sense at this budget."
        ),
        "bu_hook_top": (
            "Looking for a new smartphone under {band}? In this video we rank the top phones by "
            "their specifications and current price, and at the end you get a clear verdict on "
            "which one makes the most sense at this budget."
        ),
        "bu_intro_heading": "How the score works",
        "bu_intro": (
            "Every phone gets a score {scale}. It combines performance, display, camera, "
            "battery, software updates and build, each with a fixed weight, so every phone is "
            "judged on the same scale. Let's start from the bottom of the list."
        ),
        "p_heading": "Number {rank}: {name}",
        "p_open": "At {rank} is the {name}, currently priced at {price}. Its overall {overall}. ",
        "p_perf": (
            "For performance it uses the {chipset} chip, with a reported AnTuTu v10 {antutu}, "
            "and {ram} of RAM. "
        ),
        "p_display": (
            "The display is a {size} {dtype} panel with a {hz} refresh rate and up to {nits} "
            "peak brightness. "
        ),
        "p_cam_ois": "The camera uses a {mp} main sensor, and it {ois}. ",
        "p_cam_no_ois": "The camera uses a {mp} main sensor, but it has {ois}. ",
        "ois_yes": "has OIS",
        "ois_no": "no OIS",
        "p_uw": "There is also a {uw} ultrawide camera. ",
        "p_no_uw": "There is {uw}. ",
        "no_uw": "no ultrawide camera",
        "p_battery": "The battery is {mah}, with {w} charging. ",
        "p_software": "The company promises {os} of OS updates and {sec} of security updates. ",
        "p_build_ip": "It has an {ip} rating and weighs {g}. ",
        "p_build_no_ip": "It has {ip} and weighs {g}. ",
        "no_ip": "no IP rating",
        "p_hist": "Over the {days}, its lowest price was {low} and its highest {high}. ",
        "p_hist_flat": "Over the {days}, its price held steady at {low}. ",
        "p_strength": "Its biggest strength is {label}, where it scores {score}. ",
        "p_no_strength": "None of its categories stands out from the rest. ",
        "p_weak": "Its weak spot is {label}, with only {score}. ",
        "p_buy": "Buy it if you want {what}. ",
        "p_skip": "Skip it if {what} matters most to you.",
        "v_heading": "Verdict",
        "bu_verdict": (
            "So here is the verdict. Under {band}, {rank} is the {name}, with an overall "
            "{overall} at {price}. "
        ),
        "bu_value": (
            "If your budget is a little lower, look at the {name}, which gives {spk} for {per}, "
            "the most on this list. "
        ),
        "v_close": "Remember that prices change, so check the current price before you buy.",
        "outro_heading": "What next",
        "outro": (
            "Links to every phone are in the description. Subscribe for more data-driven buying "
            "guides."
        ),
        "ds_title": "{name} price drop: now {price}",
        "ds_hook_heading": "Price drop",
        "ds_hook": "The {name} just got cheaper!",
        "ds_price_heading": "Price",
        "ds_price": "Over the {days} it sold for up to {high}; now it is {price}, that is {drop} less. ",
        "ds_specs_heading": "Highlights",
        "ds_specs": "You get an {dtype} display, a {hz} refresh rate and a {mah} battery. ",
        "ds_verdict_good": "Verdict: under {band} it is {rank} on our list, so at this price it is a good deal.",
        "ds_verdict_meh": "Verdict: under {band} it is {rank} on our list, so check the other options first.",
        "buy": {
            "performance": "fast performance and gaming",
            "display": "a great display",
            "camera": "a dependable camera",
            "battery": "long battery life and fast charging",
            "software": "long-term updates",
            "build": "a tough, light build",
        },
        "skip": {
            "performance": "fast performance",
            "display": "a good display",
            "camera": "the camera",
            "battery": "battery life",
            "software": "long software support",
            "build": "water resistance and low weight",
        },
    },
}


class _Ctx:
    """Helpers that turn fact refs into claim parts in the brief's language."""

    def __init__(self, brief: Brief, facts: Facts, dataset: Dataset, cfg: ScoringConfig):
        self.brief, self.facts, self.dataset, self.cfg = brief, facts, dataset, cfg
        self.u = UNITS[brief.lang]
        self.t = T[brief.lang]

    def v(self, ref: str) -> FactValue:
        return self.facts[ref].value

    def c(self, ref: str, unit: str | None = None, suffix: str = "") -> C:
        value = self.v(ref)
        shown = (
            num(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else str(value)
        )
        display = (self.u[unit].format(shown) if unit else shown) + suffix
        return C(display, ref, value)

    def name(self, pid: str) -> str:
        p = self.dataset.product(pid)
        return f"{p.brand} {p.model}"

    def label(self, category: str) -> str:
        return self.cfg.categories[category].label.get(self.brief.lang, category)

    def section(self, sid, kind, heading, tpl, product_id=None, **parts) -> WriterSection:
        narration, claims = render(self.t[tpl], **parts)
        return WriterSection(
            id=sid,
            kind=kind,
            heading=heading,
            narration=narration,
            product_id=product_id,
            claims=claims,
        )


def _product_section(x: _Ctx, pid: str, band: int) -> WriterSection:
    t, sp = x.t, f"products.{pid}.specs"
    rank = x.c(f"ranking.{band}.{pid}.rank", "rank")
    parts: list[tuple[str, list[Claim]]] = []

    def add(tpl: str, **kw: str | C) -> None:
        parts.append(render(t[tpl], **kw))

    add(
        "p_open",
        rank=rank,
        name=x.name(pid),
        price=x.c(f"prices.{pid}.current", "rupees"),
        overall=x.c(f"scores.{pid}.overall", "score"),
    )
    add(
        "p_perf",
        chipset=x.c(f"{sp}.chipset"),
        antutu=x.c(f"{sp}.antutu_v10", "score"),
        ram=x.c(f"{sp}.ram_gb", suffix=" GB"),
    )
    dtype = x.v(f"{sp}.display_type")
    add(
        "p_display",
        size=x.c(f"{sp}.display_size_in", "inch"),
        dtype=C(str(dtype).upper(), f"{sp}.display_type", dtype),
        hz=x.c(f"{sp}.refresh_rate_hz", suffix="Hz"),
        nits=x.c(f"{sp}.peak_brightness_nits", "nits"),
    )
    ois = bool(x.v(f"{sp}.main_camera_ois"))
    add(
        "p_cam_ois" if ois else "p_cam_no_ois",
        mp=x.c(f"{sp}.main_camera_mp", "mp"),
        ois=C(t["ois_yes" if ois else "ois_no"], f"{sp}.main_camera_ois", ois),
    )
    if x.v(f"{sp}.ultrawide_mp"):
        add("p_uw", uw=x.c(f"{sp}.ultrawide_mp", "mp"))
    else:
        add("p_no_uw", uw=C(t["no_uw"], f"{sp}.ultrawide_mp", 0))
    add(
        "p_battery",
        mah=x.c(f"{sp}.battery_mah", suffix="mAh"),
        w=x.c(f"{sp}.charging_w", suffix="W"),
    )
    add(
        "p_software",
        os=x.c(f"{sp}.os_updates_years", "years"),
        sec=x.c(f"{sp}.security_updates_years", "years"),
    )
    ip = str(x.v(f"{sp}.ip_rating"))
    if ip == "none":
        add(
            "p_build_no_ip",
            ip=C(t["no_ip"], f"{sp}.ip_rating", ip),
            g=x.c(f"{sp}.weight_g", "grams"),
        )
    else:
        add("p_build_ip", ip=C(ip.upper(), f"{sp}.ip_rating", ip), g=x.c(f"{sp}.weight_g", "grams"))
    # Price history is narrated for the top 3 only, keeping long-form under 9 minutes;
    # the on-screen chart shows it for every phone.
    if x.v(f"ranking.{band}.{pid}.rank") <= 3:  # type: ignore[operator]
        lo, hi = x.v(f"prices.{pid}.lowest_90d"), x.v(f"prices.{pid}.highest_90d")
        days = x.c("meta.history_days", "days")
        low = x.c(f"prices.{pid}.lowest_90d", "rupees")
        if lo != hi:
            add("p_hist", days=days, low=low, high=x.c(f"prices.{pid}.highest_90d", "rupees"))
        else:
            add("p_hist_flat", days=days, low=low)

    s = score_product(x.dataset.product(pid), x.cfg)
    if s.strengths:
        cat = s.strengths[0]
        add("p_strength", label=x.label(cat), score=x.c(f"scores.{pid}.{cat}", "points"))
    else:
        add("p_no_strength")
    if s.weaknesses:
        cat = s.weaknesses[0]
        add("p_weak", label=x.label(cat), score=x.c(f"scores.{pid}.{cat}", "points"))
    if s.strengths:
        add("p_buy", what=t["buy"][s.strengths[0]])
    if s.weaknesses:
        add("p_skip", what=t["skip"][s.weaknesses[0]])

    heading, hclaims = render(
        t["p_heading"], rank=num(x.v(f"ranking.{band}.{pid}.rank")), name=x.name(pid)
    )
    assert not hclaims
    return WriterSection(
        id=f"product-{pid}",
        kind="product",
        heading=heading,
        narration="".join(p[0] for p in parts).strip(),
        product_id=pid,
        claims=[c for p in parts for c in p[1]],
    )


def write_best_under(x: _Ctx) -> WriterScript:
    b = x.brief
    band = b.band_inr
    assert band is not None
    band_c = x.c(f"ranking.{band}.band", "rupees")
    count = x.v(f"ranking.{band}.count")
    if count == len(b.product_ids):
        hook = x.section(
            "hook",
            "hook",
            x.t["bu_title"].format(band=band_c.display),
            "bu_hook",
            band=band_c,
            count=x.c(f"ranking.{band}.count", "phones"),
        )
    else:
        hook = x.section(
            "hook", "hook", x.t["bu_title"].format(band=band_c.display), "bu_hook_top", band=band_c
        )
    intro = x.section(
        "intro",
        "intro",
        x.t["bu_intro_heading"],
        "bu_intro",
        scale=x.c("meta.score_scale", "scale"),
    )
    products = [_product_section(x, pid, band) for pid in reversed(b.product_ids)]

    winner = b.product_ids[0]
    vtext, vclaims = render(
        x.t["bu_verdict"],
        band=band_c,
        rank=x.c(f"ranking.{band}.{winner}.rank", "rank"),
        name=x.name(winner),
        overall=x.c(f"scores.{winner}.overall", "score"),
        price=x.c(f"prices.{winner}.current", "rupees"),
    )
    best_value = max(b.product_ids, key=lambda p: (x.v(f"ranking.{band}.{p}.score_per_10k"), p))
    if best_value != winner:
        t2, c2 = render(
            x.t["bu_value"],
            name=x.name(best_value),
            per=x.c("meta.value_per_inr", "per"),
            spk=x.c(f"ranking.{band}.{best_value}.score_per_10k", "points"),
        )
        vtext, vclaims = vtext + t2, vclaims + c2
    vtext += x.t["v_close"]
    verdict = WriterSection(
        id="verdict",
        kind="verdict",
        heading=x.t["v_heading"],
        narration=vtext,
        product_id=winner,
        claims=vclaims,
    )
    outro = x.section("outro", "outro", x.t["outro_heading"], "outro")
    title, _ = render(x.t["bu_title"], band=band_c)
    return WriterScript(title=title, sections=[hook, intro, *products, verdict, outro])


def write_deal_short(x: _Ctx) -> WriterScript:
    b = x.brief
    pid, band = b.product_ids[0], b.band_inr
    assert band is not None
    drop = x.v(f"prices.{pid}.drop_from_high_90d")
    if not isinstance(drop, int) or drop <= 0:
        raise TemplateUnsupported(f"{pid}: no price drop in the last 90 days")
    sp = f"products.{pid}.specs"
    price = x.c(f"prices.{pid}.current", "rupees")
    name = x.name(pid)
    hook = x.section("hook", "hook", x.t["ds_hook_heading"], "ds_hook", product_id=pid, name=name)
    price_s = x.section(
        "price",
        "price",
        x.t["ds_price_heading"],
        "ds_price",
        product_id=pid,
        days=x.c("meta.history_days", "days"),
        high=x.c(f"prices.{pid}.highest_90d", "rupees"),
        price=price,
        drop=x.c(f"prices.{pid}.drop_from_high_90d", "rupees"),
    )
    dtype = x.v(f"{sp}.display_type")
    specs = x.section(
        "specs",
        "product",
        x.t["ds_specs_heading"],
        "ds_specs",
        product_id=pid,
        dtype=C(str(dtype).upper(), f"{sp}.display_type", dtype),
        hz=x.c(f"{sp}.refresh_rate_hz", suffix="Hz"),
        mah=x.c(f"{sp}.battery_mah", suffix="mAh"),
    )
    rank_v = x.v(f"ranking.{band}.{pid}.rank")
    tpl = "ds_verdict_good" if isinstance(rank_v, int) and rank_v <= 3 else "ds_verdict_meh"
    verdict = x.section(
        "verdict",
        "verdict",
        x.t["v_heading"],
        tpl,
        product_id=pid,
        band=x.c(f"ranking.{band}.band", "rupees"),
        rank=x.c(f"ranking.{band}.{pid}.rank", "rank"),
    )
    title, _ = render(x.t["ds_title"], name=name, price=price)
    return WriterScript(title=title, sections=[hook, price_s, specs, verdict])


WRITERS = {"best_under": write_best_under, "deal_short": write_deal_short}


def write_from_template(
    brief: Brief, facts: Facts, dataset: Dataset, cfg: ScoringConfig
) -> WriterScript:
    fn = WRITERS.get(brief.format)
    if fn is None:
        raise TemplateUnsupported(
            f"no template for {brief.format!r}; use the LLM writer (--writer llm)"
        )
    return fn(_Ctx(brief, facts, dataset, cfg))
