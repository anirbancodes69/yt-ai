# Voice and render (Phase 3)

```
fact-checked script ─▶ sentences ─▶ TTS (cached, costed) ─▶ narration.wav + timeline
                                                  │
                    shot plan (visual per sentence, from its claims)
                                                  │
        frames (Pillow+raqm, matplotlib) ─▶ ffmpeg concat ─▶ + captions (libass) + music ─▶ video.mp4
```

`channel render <video_id>` first calls `require_passed()`: a script that failed the
fact-check, was edited afterwards, or was checked against older data **cannot be rendered**.

## Voice
* `TTSProvider` interface ([voice/tts.py](../src/channel/voice/tts.py)).
  * `google` — Google Cloud TTS, voice from `tts.google.voices` (default `hi-IN-Neural2-B`).
    Needs `GOOGLE_APPLICATION_CREDENTIALS`. Put the service-account JSON under the repo
    (e.g. `secrets/gcp.json`, git-ignored) and set the path as seen **inside the container**
    (`/app/secrets/gcp.json`).
  * `espeak` — offline espeak-ng, robotic. Used by `make demo` and tests so no account is needed.
* The script is voiced **sentence by sentence**. That gives exact caption and shot timings and
  lets the cache reuse unchanged sentences when a script is edited.
* Pronunciation (`pronounce:` in [config/render_text.yaml](../config/render_text.yaml)):
  digit commas dropped, units and acronyms spelled out. Captions keep the original text.
* Cost: audio is cached on disk by (voice, text). Characters and cost are logged to the same
  ledger as Claude calls, with the monthly free allowance applied. Paid TTS respects the
  monthly and per-video budget caps; cache hits are free.
* Length: formats have `target_seconds` (long-form 6–9 min, Short 30–50 s). If the narration
  is outside the window it is re-timed with `atempo` (pitch preserved) within 0.9×–1.2×.
  Beyond that rendering fails and the script should be edited. `render.json` records the tempo.

## Visuals
All frames come from data and the fact table, never from narration text
([render/frames.py](../src/channel/render/frames.py)):

| Card | Shows |
|---|---|
| title | video heading + "price as of <date>" |
| notice | disclosure / method / SAMPLE text on screen while it is spoken |
| weights | what the score is made of (animated bars) |
| product | press image (or placeholder), rank, current price, overall score |
| scorecard | six category bars (red/blue/green by thresholds) with the key specs; the category being discussed is highlighted; animated on first appearance |
| price chart | matplotlib chart of the last 90 days |
| buy-if / skip-if | from each product's strengths and weaknesses |
| ranking | animated ranked bars; winner in gold |
| compare | mirrored bars, category winner highlighted |
| deal | price tag with the struck-through old price |
| outro | subscribe + "links in the description" |

Every frame carries the footer "prices as of <date> • based on specs, prices and published
reviews". SAMPLE data adds a red badge and a diagonal watermark to **every** frame.

The five formats differ in theme and layout ([render/theme.py](../src/channel/render/theme.py)):
navy countdown (best-under), red/blue split (head-to-head), violet with ribbon (launch),
warm black + yellow tag, vertical (deal Short), green with rank arrows (monthly).
`out/test_frames/formats_contact_sheet.png` shows them side by side.

Which card is on screen follows what the sentence cites
([render/shots.py](../src/channel/render/shots.py)): price-history claims → chart, spec or
category-score claims → scorecard, sentences after the last claim in a product section →
buy-if/skip-if.

## Captions
ASS file burned in by ffmpeg/libass: short chunks that pop in, with a word-by-word karaoke fill.
A plain `captions.srt` is written for upload. ffmpeg uses `shaping=complex` (HarfBuzz); the
default "simple" shaper puts Devanagari pre-base matras on the wrong side ("आधारित" → "आधारति").

## Devanagari proof
`channel proofs` (also part of `make demo`) writes into `out/test_frames/`:
* `devanagari.png` — conjuncts, matras and mixed Latin/Devanagari drawn with Pillow's raqm engine
  (correct) above the BASIC engine (broken), plus a check that raqm is present.
* `captions_devanagari.png` — the same caption burned with libass simple (top) vs complex (bottom).
Tests assert both engines produce different output, so a build without raqm fails loudly.

Noto Sans Devanagari has no Latin letters or symbols such as `·` and `•`, so text is split into
script runs and each run uses the right font on a shared baseline.

## Music
Drop MP3/WAV/M4A files into `assets/music/` (git-ignored). One is picked deterministically per
video, looped, set to `render.music_volume_db`, and **ducked** while the voice speaks
(sidechain compression). Without files the video has narration only. Use music you have the
rights to.

## Press images
Product `press_images:` entries (filenames in `assets/press/`) are shown on product cards.
With none (SAMPLE data), a labelled placeholder phone is drawn. Only supply official press images
you are allowed to use.

## Output
```
out/videos/<video_id>/  video.mp4  narration.wav  captions.ass/.srt  timeline.json
                        render.json (duration, size, tempo, shot plan)  cost.json (API cost for this video)
```

## Limits
* espeak is intelligible but not publishable; the real Neural2 voice has not been heard yet
  (no credentials). Its timing and pronunciation may need tuning.
* Frames are stills with simple animation (bars growing, crossfades). No video clips by design.
* An ffmpeg render of the 9-minute video takes about 1.5 minutes.
