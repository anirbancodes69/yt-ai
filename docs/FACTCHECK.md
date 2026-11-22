# Scripts and fact-check

## Flow
```
brief (format + inputs) ─▶ writer ─▶ + system sections ─▶ fact-check ─▶ out/videos/<id>/
                            │                               │
                 template (offline) or                 fail → re-prompt Claude with the
                 Claude (structured JSON)              errors (llm.fix_attempts), then BLOCK
```

* **Brief** ([briefs.py](../src/channel/scripts/briefs.py)) — which products a video
  covers and who the verdict must name. The fact-checker rebuilds it from current data
  and rejects a script whose brief differs.
* **Fact table** ([facts.py](../src/channel/facts.py)) — every value a script may state,
  keyed by reference (`products.<id>.specs.battery_mah`, `prices.<id>.current`,
  `scores.<id>.overall`, `ranking.<band>.<id>.rank`, …). Scores are stored at the
  precision they are spoken (1 decimal), so comparisons are exact.
* **Writers**
  * Template writer ([templates.py](../src/channel/scripts/templates.py)): deterministic
    Hindi/English for `best_under` and `deal_short`. No key, no cost; used by
    `make demo`, the tests, and as a fallback when the budget is spent.
  * LLM writer ([llm.py](../src/channel/scripts/llm.py), [prompts.py](../src/channel/scripts/prompts.py)):
    Claude (`llm.model` in settings) with `output_config.format` = the `WriterScript`
    JSON schema, effort from settings, and server-side refusal fallback
    (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). All five formats.
* **System sections** ([text.py](../src/channel/scripts/text.py), wording in
  [config/script_text.yaml](../config/script_text.yaml)): affiliate disclosure (spoken),
  method note ("based on specs, prices and published reviews… prices as of <date>"),
  and a SAMPLE notice when the data is sample. Inserted right after the hook by code;
  writers never produce them.

## What the fact-checker enforces
Rules live in [checker.py](../src/channel/factcheck/checker.py) and
[config/factcheck.yaml](../config/factcheck.yaml). Any error means status `fail`.

| Code | Meaning |
|---|---|
| `unknown_ref` | claim cites a key that is not in the fact table |
| `value_mismatch` | claimed value ≠ data |
| `claim_text_not_found` | claim text is not a verbatim span of the narration |
| `claim_number_mismatch` | the number spoken in the claim text ≠ data; a true/false claim that disagrees with negation words; a zero stated without a negation |
| `unbacked_number` | a number in narration, heading or title that no verified claim covers (Devanagari digits and "लाख/हज़ार" are understood) |
| `banned_phrase` | implies hands-on testing ("हमने टेस्ट", "मैंने इस्तेमाल", "hands-on"…) |
| `missing_system_section` / `system_section_modified` | disclosure, method note or SAMPLE notice missing or edited |
| `verdict_missing_winner` | the verdict does not cite a fact about the expected winner |
| `off_brief_product` | talks about a product that isn't part of this video |
| `structure` | no hook first, no verdict, product talk after the verdict, a ranked product skipped |
| `brief_mismatch`, `sample_flag`, `stale_script` | brief, SAMPLE flag or data fingerprint doesn't match current data |

Length outside the format's word target is a **warning**, not an error.

Product names, chipset names and tokens like "5G"/"AnTuTu v10" may contain digits
without being claims.

## The gate
`channel.factcheck.require_passed(video_dir, data_files)` returns the script only if
`factcheck.json` says `pass`, its hash matches the script byte-for-byte, and the data
and config files haven't changed since. Render (Phase 3) and upload (Phase 5) call it
first.

## Cost control
* SQLite at `cache/channel.sqlite`: identical requests are served from cache and
  logged at ₹0.
* Every paid call goes into `cost_ledger` with tokens, USD and INR, priced by the model
  that actually served it. A model missing from the pricing table is charged at the
  highest listed rate and flagged.
* A new paid call is refused once the monthly cap (₹3,000) or the per-video cap
  (₹150) is reached. `channel costs` shows the ledger.

## Known limitations
* Numbers written as words ("दो साल") can't be detected reliably in Hindi, because
  "एक" also means "a". The prompt requires digits; the template writer always uses
  digits.
* Non-numeric claims (display type, IP rating) are checked for value and wording, but
  the checker cannot judge free-form opinions. The prompt restricts opinions to what
  the scores support.
