# PLAN — Automated Hindi buying-guide channel

Status: Phase 3 complete, awaiting review. Each phase ends with a stop for review.

## 1. What I understand we are building

A batch pipeline that turns **structured product data** into faceless YouTube
videos for Indian phone/gadget shoppers. One video = one purchase decision with
a clear verdict. The pipeline is:

```
products.yaml + prices.csv (via PriceProvider)
        │
        ▼
   data/ (validated pydantic models, every fact sourced)
        │
        ▼
   scoring/ (deterministic, config-driven, explainable)
        │
        ▼
   scripts/ (Claude writes structured JSON script: sections + claims w/ data refs)
        │
        ▼
   factcheck/ (every claim + every number in narration checked against data → BLOCK on mismatch)
        │
        ▼
   voice/ (TTSProvider → Google Cloud TTS)  +  render/ (Pillow/matplotlib frames → ffmpeg mp4)
        │
        ▼
   packaging/ (title, description w/ disclosure first, chapters, tags, thumbnail)
        │
        ▼
   upload/ (dry-run default → private → scheduled; PAUSE file blocks; upload-ready folder fallback)
        │
        ▼
   analytics/ (weekly CTR/retention report → re-rank topic queue)
```

## 2. Non-negotiables and how each is enforced

| Constraint | Enforcement point |
|---|---|
| Every fact has `source_url` + `fetched_at` | Pydantic models make both required; dataset fails to load otherwise. |
| Every number in the script comes from data | Script JSON carries `claims[]` with `data_ref` paths; fact-checker resolves each ref and compares values. It also scans narration text for **any** number not covered by a verified claim. Mismatch → non-zero exit, render/upload refuse to run without a passing fact-check report whose hash matches the script. |
| No hands-on claims | Fixed disclaimer section injected by code (not by the LLM); fact-checker also has a banned-phrase list ("maine use kiya", "hands-on", "humne test kiya", …). |
| "Price as of <date>" on screen | Renderer pulls the date from the PricePoint used; every price overlay gets it. |
| Affiliate disclosure first line + spoken | Description builder hard-codes it as line 1; script builder injects a spoken disclosure section in code. |
| No Amazon/Flipkart scraping | Only `PriceProvider` implementations: `ManualCSVProvider` (real), `AmazonPAAPIProvider` / `FlipkartAffiliateProvider` (stubs raising `NotImplementedError`). No HTTP code against those sites. |
| SAMPLE data labelled | `sample: true` on every sample record; dataset validator refuses to mix sample and non-sample; renderer stamps a "SAMPLE DATA" watermark whenever any input is sample; uploader refuses `--publish` on sample data. |
| Dry-run default; private unless `--publish`; `PAUSE` blocks | Upload module checks in this order: PAUSE file → dry-run flag → privacy. Tested. |
| Secrets in `.env` only | `.env` in `.gitignore`; `.env.example` committed; settings read via env. |
| Budget < ₹3,000/month | Every external call goes through a cache (SQLite, keyed by request hash) and a cost logger (per-video ledger). A monthly budget cap in settings makes the pipeline refuse new paid calls once exceeded. |
| Hindi rendering correct | Pillow with `libraqm` layout engine (`ImageFont.Layout.RAQM`); a test asserts raqm is available and renders a test frame to `out/test_frames/devanagari.png`. |

## 3. Assumptions (stated so you can correct them)

1. **Sample phones use fictional brand names** (e.g. "SAMPLE Zentra 5G") with
   `https://example.com/sample/...` source URLs. That makes it impossible to
   mistake them for real products or real prices. Real data replaces this later.
2. **Source of truth for products is YAML + CSV in `data/`**, versioned in git.
   SQLite holds derived/operational state only: API cache, cost ledger, topic
   queue, upload log, analytics. This keeps data reviewable in diffs.
3. **Scoring uses fixed anchor ranges from `config/scoring.yaml`**, not
   min/max over the current dataset. A phone's score doesn't change because
   another phone was added — required for monthly refresh videos to be
   consistent.
4. **"Current price"** = lowest price among each retailer's most recent
   observation within `max_price_age_days` (default 7). Older prices are
   treated as stale and the product is excluded from price-band lists.
5. **Price bands are inclusive upper bounds** (≤ ₹20,000 is "under 20k"). A
   phone at ₹20,499 is not in the 20k list.
6. **Numbers in Hindi narration use Western digits** (common on Hindi tech
   YouTube, and what TTS reads reliably). Configurable later.
7. **Claude model defaults to `claude-opus-5-5`**, set in `config/settings.yaml`.
   Cost estimate: ~10k input + ~6k output tokens per long-form script ≈ $0.16
   (~₹14); with one fact-check retry and 7 Shorts/week the monthly LLM spend
   is well under ₹1,000. You can switch to a cheaper model in config.
8. **Google Cloud TTS** free tier covers our volume (~110k chars/month for
   12 long-form + 30 Shorts). Exact free-tier limits per voice type to be
   verified in Phase 3 before we choose the voice.
9. **YouTube Data API** quota (10,000 units/day, ~1,600 per upload) allows
   ~6 uploads/day — enough for the cadence.
10. **Everything runs in Docker** (user requirement). Image:
    `python:3.12-slim-bookworm` + uv + ffmpeg + libraqm + `fonts-noto-core`
    (Noto Sans Devanagari). Make targets wrap `docker compose run --rm app`.
    The host needs only Docker. The GitHub Actions workflow (Phase 6) will
    use the same image.
11. No web framework, no queue service — a CLI (`channel ...`) plus Make targets
    and a GitHub Actions cron. Fewer moving parts, cheaper.

## 4. Open questions (not blocking — defaults chosen above)

1. ~~ffmpeg missing on host~~ — resolved: ffmpeg 5.1, libraqm and Noto
   Devanagari ship in the Docker image.
2. Affiliate IDs: Amazon Associates tag and Flipkart affiliate ID — needed only
   for Phase 4 config; placeholders until then.
3. Google Cloud project for TTS and YouTube OAuth client — needed in Phase 3/5.
4. Do you have Amazon PA-API access? (It needs 3 qualifying sales before keys
   work, so the CSV provider will likely be primary for a while.)
5. Voice preference (male/female; Google `hi-IN` voices) — Phase 3.
6. Do you want the git repo initialised now? (It's not a git repo yet.)

## 5. Phase breakdown

### Phase 1 — Data and scoring
- `channel.data.models`: `Spec`, `Product`, `PricePoint`, `Dataset` (pydantic v2).
- `channel.data.loader`: load YAML/CSV, cross-validate (prices reference known
  products, sample flags consistent).
- `channel.data.prices`: `PriceProvider` protocol, `ManualCSVProvider`, stubs.
- `channel.scoring`: config model for `scoring.yaml`, deterministic scorer with
  per-metric breakdown, strengths/weaknesses, band ranking, head-to-head.
- `config/settings.yaml`, `config/scoring.yaml`, `config/formats.yaml` (bands).
- SAMPLE dataset of 8 phones + price history.
- CLI: `channel validate`, `channel rank --band 20000`, `channel compare A B`,
  `channel explain ID`.
- `make demo` (Phase 1 form): validate data, write rankings for every band to
  `out/demo/`.

### Phase 2 — Script generation and fact-check  ✅
Decisions taken: a deterministic template writer (best_under, deal_short) for
offline demo/tests and as a budget fallback; claims cite a fact table rebuilt
independently by the checker; disclosure/method/SAMPLE text inserted by code;
refusal fallback (`fallbacks: "default"`) enabled on Claude calls. Details in
docs/FACTCHECK.md.

- Script JSON schema (pydantic) → `output_config.format` structured output.
- Claims: `{text, data_ref: "products.<id>.specs.battery_mah", value}`; prices
  reference a specific PricePoint.
- SQLite response cache keyed by (model, prompt hash, schema hash); cost ledger.
- Fact-checker: resolve refs, compare normalised values, scan free text for
  unbacked numbers and banned phrases; machine-readable report; non-zero exit.
- Tests with deliberately wrong claims (wrong battery, wrong price, invented
  number in narration, hands-on phrasing, missing disclosure).
- An offline "fixture" script so `make demo` works without an API key.

### Phase 3 — Voice and render  ✅ (see docs/RENDER.md)
- `TTSProvider` protocol, Google Cloud TTS impl, cached by text hash; an
  offline silent/beep provider for tests and keyless demo.
- Frames: scorecards, price-history chart, ranked bars, buy-if/skip-if cards,
  animated captions, press images, SAMPLE watermark, "price as of" overlays.
- ffmpeg assembly with music ducking. Five visually distinct format templates.
- `make demo` → one 6–9 min long-form + one vertical Short from sample data.

### Phase 4 — Packaging
- Title/description/tags/chapters per format; disclosure line 1; affiliate
  links from config; code-generated thumbnail; length/char-limit validation.

### Phase 5 — Topic engine and upload
- Weekly queue from `data/launch_calendar.yaml` + price bands; cadence 3 long +
  7 Shorts/week, launch-day priority slot.
- YouTube uploader (dry-run, private, scheduled); `docs/UPLOAD.md` on the
  unaudited-project private-only restriction; upload-ready folder fallback.

### Phase 6 — Scheduling and analytics
- `channel run --weekly` local command + GitHub Actions workflow.
- YouTube Analytics pull → weekly CTR/retention report → topic re-ranking.

## 6. Definition of done (every phase)
Tests pass, `ruff check` and `ruff format --check` clean, `make demo` works,
README updated, summary with assumptions and gaps.
