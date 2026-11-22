# Scoring method

Source of truth: [`config/scoring.yaml`](../config/scoring.yaml). Code:
[`src/channel/scoring/engine.py`](../src/channel/scoring/engine.py).

## Properties
- **Deterministic** — no randomness, no LLM, ties broken by price then id.
- **Absolute, not relative** — each metric maps a spec onto [0, 1] using fixed
  anchors (`min`/`max`) from the config. Adding or removing a phone never
  changes another phone's score, so monthly refresh videos stay consistent.
- **Explainable** — `channel score <id>` prints every metric's raw value,
  normalised value and weight; contributions sum exactly to the overall score.
  Every metric carries a `data_ref` (`products.<id>.specs.<key>`) and the
  spec's `source_url`, which the Phase 2 fact-checker reuses.
- **Versioned** — `version` plus a 12-char fingerprint of the YAML file are
  attached to every score, so a video can say which method produced it.

## Formula
| Step | Rule |
|---|---|
| linear metric | `x = clamp((value − min) / (max − min), 0, 1)`; if `higher_is_better: false`, `x = 1 − x` |
| map metric | `x = map[lower(value)]`; an unknown value is an error (no silent zero) |
| bool metric | `x = 1` if true else `0` |
| category score | `10 × Σ(x × metric_weight)` → 0..10 |
| overall | `Σ(category_score × category_weight)` → 0..10 |
| strengths | categories ≥ 7.5, best first, max 2 |
| weaknesses | categories ≤ 5.0, worst first, max 2 |
| ranking in a band | current price ≤ band (inclusive); overall desc → price asc → id |
| head-to-head | category winner only if the difference ≥ `tie_margin` (0.3) |

## Category weights (v1)
| Category | Weight | Metrics |
|---|---|---|
| Performance | 0.25 | AnTuTu v10 (0.8), RAM (0.2) |
| Display | 0.20 | panel type (0.4), refresh rate (0.3), peak brightness (0.3) |
| Camera | 0.20 | OIS (0.4), main MP (0.3), ultrawide MP (0.3) |
| Battery | 0.20 | capacity (0.6), charging watts (0.4) |
| Software | 0.10 | OS update years (0.6), security update years (0.4) |
| Build | 0.05 | IP rating (0.6), weight, lighter is better (0.4) |

## Worked example — SAMPLE Zentra Z5, performance
AnTuTu 420,000 → (420,000 − 300,000) / 1,200,000 = 0.10 × 0.8 = 0.08.
RAM 4 GB → (4 − 4) / 8 = 0 × 0.2 = 0. Category = 10 × 0.08 = **0.80**.
(This exact calculation is asserted in `tests/test_scoring.py`.)

## Known limitations
- Spec-based scoring can't measure real-world camera quality, thermals or
  software polish. Videos must say they're based on specs, prices and
  published reviews; that wording is enforced in Phase 2.
- Absolute anchors mean budget phones score low (₹10k winner ≈ 2.4/10).
  That's honest, but how to present it on screen is an open question (see
  PLAN.md).

## Changing the method
Edit `config/scoring.yaml`, bump `version`, run `make check`. The loader
rejects weights that don't sum to 1, `min ≥ max`, unknown spec keys, and
mismatched metric/spec types.
