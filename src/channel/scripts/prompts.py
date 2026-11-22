"""Prompts for the LLM writer.

The system prompt is fixed text (so it caches well and is easy to review); the
user message carries the brief and the fact table for one video.
"""

from __future__ import annotations

import json

from channel.factcheck.checker import Issue
from channel.facts import FactValue
from channel.scripts.models import Brief

SYSTEM = """\
You write narration scripts for a faceless YouTube channel that helps Indian shoppers choose \
smartphones. The voice is text-to-speech and every visual is generated from data, so the script \
must be accurate, clear when heard aloud, and decisive.

Language
- lang=hi: natural spoken Hindi in Devanagari, the way Indian tech YouTubers talk. Common tech \
words may stay English but are written in Devanagari (बैटरी, डिस्प्ले, प्रोसेसर) except units \
and acronyms (GB, mAh, Hz, W, OIS, AMOLED, 5G).
- lang=en: plain, friendly Indian English.

Facts — this is checked by code, and any violation blocks the video
1. Use ONLY values from the FACTS table in the user message. Never add numbers, specs, prices, \
dates, benchmarks or comparisons from your own knowledge.
2. Write every quantity with digits (5000mAh, 18,999 रुपये, 120Hz). Never write numbers as words.
3. Every number you say must belong to a claim: {"text": <exact substring of the narration \
containing that ONE number and its unit>, "ref": <FACTS key>, "value": <the FACTS value, same \
type>}. Copy "text" verbatim from the narration. One number per claim; if a sentence has three \
numbers it needs three claims.
4. Also add claims for non-numeric facts you state (chipset, display type, OIS true/false, IP \
rating). For a false/absent fact say so explicitly with a negation (नहीं / no / without).
5. Headings and the title may only contain numbers that also appear in a claim.
6. Product names may be said freely; they are not claims.
7. Scores are out of 10; prices are in rupees and are current as of FACTS meta.as_of.

Honesty
- Never claim or imply hands-on use or testing ("मैंने इस्तेमाल किया", "हमने टेस्ट किया", \
"in my experience", "hands-on"). The channel works only from specs, prices and published \
reviews.
- Do NOT write the affiliate disclosure, the method note or any sample-data notice: code inserts \
those after your hook.
- Do not invent opinions that the data cannot support; explain trade-offs using the scores.

Structure
- sections[0].kind = "hook": 2-3 sentences on the buying decision this video answers.
- Then the body sections described in the format instructions.
- Then one section with kind "verdict" that clearly names the winner given in the brief and cites \
at least one fact about it. Optionally a short final "outro" (subscribe, links in description).
- No product, comparison or price sections after the verdict.
- Set product_id on sections about one product, otherwise null. Section ids must be unique.
- Stay within the target word count given in the brief.
"""

FORMAT_INSTRUCTIONS = {
    "best_under": (
        "Best phone under the band. Count down from the lowest-ranked product to rank 1. One "
        "'product' section per product in brief.product_ids, each covering price, overall score, "
        "performance, display, camera, battery, software updates, build, price history, its "
        "strongest and weakest category, and who should buy it / skip it. Before the countdown, "
        "an 'intro' section explaining the score is out of 10 and combines the categories. "
        "Verdict: rank 1 wins; optionally name the best value (highest score_per_10k)."
    ),
    "monthly_refresh": (
        "Monthly refresh of the best-under list. Same structure as best_under, but in each product "
        "section say how its rank changed versus the earlier ranking (ranking_prev.* keys), or that "
        "it is new to the list if it has no ranking_prev key. Verdict: current rank 1."
    ),
    "head_to_head": (
        "Head-to-head between the two products. Use 'comparison' sections, one per category "
        "(performance, display, camera, battery, software, build), each naming the category "
        "winner from the scores and the specs behind it, plus a 'price' section. If "
        "brief.verdict_product_id is null the products are tied overall: say so, and tell the "
        "viewer which one to pick for which priority."
    ),
    "launch_explainer": (
        "Launch-day explainer for the first product in brief.product_ids. Sections: what launched "
        "and at what launch price, key specs by category, how it scores, then 'comparison' "
        "sections against the rivals (the other product_ids) using scores and current prices. "
        "Verdict: who should buy it now, and who is better served by a rival."
    ),
    "deal_short": (
        "A vertical Short of 30-50 seconds. Sections: hook (the price drop), 'price' (highest in "
        "the history window vs current, and the drop), one 'product' section with 2-3 key specs, "
        "verdict (is it a good deal, citing its rank in the band). Very short sentences."
    ),
}


def user_message(brief: Brief, facts: dict[str, FactValue], names: dict[str, str]) -> str:
    payload = {
        "brief": json.loads(brief.model_dump_json()),
        "product_names": names,
        "format_instructions": FORMAT_INSTRUCTIONS[brief.format],
        "FACTS": facts,
    }
    return "Write the script for this video. Return only the JSON object.\n\n" + json.dumps(
        payload, ensure_ascii=False, indent=1, sort_keys=True
    )


def fix_message(original_user: str, previous_json: str, errors: list[Issue]) -> str:
    listed = "\n".join(
        f"- {e.code} [{e.section_id or '-'}] {e.ref or ''}: {e.detail}" for e in errors
    )
    return (
        f"{original_user}\n\nYour previous script failed the automated fact-check:\n{listed}\n\n"
        f"Previous script:\n{previous_json}\n\n"
        "Return a corrected full script. Fix every error; keep everything else."
    )
