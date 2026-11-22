# Packaging (Phase 4)

`channel package <video_id>` builds everything YouTube needs besides the MP4, in code, with no
API call and no cost:

```
out/videos/<video_id>/package/
    package.json          title, 2 title variants, description, tags, hashtags, chapters, links, flags
    description.txt      the description exactly as it will be uploaded
    thumbnail.png        1280x720 (1080x1920 cover for Shorts)
    package_check.json   pass/fail, tied by hash to this package and this script
```

Packaging requires a **fact-checked** script (`require_passed`). Long-form videos must be rendered
first, because chapters come from the real narration timeline (`timeline.json`).

## What is generated
* **Title** ([config/packaging.yaml](../config/packaging.yaml)): two templates per format and language;
  `--variant 1` picks the second (kept for CTR experiments in Phase 6). SAMPLE data always gets a
  `[SAMPLE]` prefix. Numbers are filled from the fact table, e.g. the band, the current price, the drop.
* **Description**, in this order:
  1. the affiliate disclosure, **line 1, exactly** (the video also says it aloud);
  2. the SAMPLE notice, when the data is sample;
  3. a one-line summary of the verdict, then the method note ("based on specs, prices and published
     reviews... prices as of <date>"), the same wording the narration speaks;
  4. product links, each with its current price and "price as of <date>";
  5. chapters (long-form only); 6. hashtags.
* **Affiliate links**: each product has `listings` (retailer + plain URL) in the data. The affiliate
  parameter is added from the environment (`AMAZON_ASSOCIATES_TAG` -> `?tag=`,
  `FLIPKART_AFFILIATE_ID` -> `?affid=`; see `affiliate.retailers` in the config). The retailer with the
  current price comes first. If an ID is not set the link is plain and a warning says so;
  `sample_value` is used **only for SAMPLE data**, so a real run never invents an ID.
* **Tags**: common + format tags + product and brand names, trimmed to YouTube's 500-char limit.
* **Chapters**: the opening sections (hook, disclosure, method, sample notice) form one "intro"
  chapter at 0:00, then one chapter per product/verdict named by the section heading. Chapters under
  10 seconds merge into the previous one (YouTube's minimum).
* **Thumbnail** ([packaging/thumbnail.py](../src/channel/packaging/thumbnail.py)): one layout per
  format, large text, the phone, price and rank, drawn with the same Devanagari-capable fonts. Shows
  "price as of <date>", plus a SAMPLE badge and watermark. About 90 KB each (limit 2 MB).

## What the package check enforces
Any error means `fail`, and the upload gate refuses it.

| Code | Rule |
|---|---|
| `disclosure_not_first` | line 1 of the description is not exactly the disclosure |
| `title_too_long`, `title_empty`, `title_sample` | 100-char limit; SAMPLE marked on sample data only |
| `description_too_long` | YouTube's limit is **5000 bytes**, and Devanagari is 3 bytes/char |
| `angle_brackets` | YouTube rejects `<` and `>` in title, description and tags |
| `missing_price_date`, `missing_sample_notice`, `missing_link` | "price as of" note, SAMPLE notice, a link per product |
| `tags_too_long`, `tag_too_long`, `too_many_hashtags` | 500 chars total, 30 per tag, 15 hashtags (YouTube ignores all above 15) |
| `unbacked_number` | a number in title/description/tags that is not a fact value for this video (product names, URLs and timestamps are masked first) |
| `banned_phrase` | the same hands-on phrases as the script fact-check |
| `chapters` | long-form needs 3+ chapters, starting at 0:00, each at least 10 s |
| `thumbnail_*` | present, exactly 1280x720 (1080x1920 for Shorts), under 2 MB |

Soft warnings: title over 70 characters (cut off in search), no link carrying an affiliate tag, a
product with no retailer link, an affiliate ID missing from the environment.

## The upload gate
`require_package(video_dir, data_files)` passes only if the script fact-check passed and is current,
the package check passed, neither the script nor the package files (including the thumbnail) changed
since, the package is not a preview, and a long-form `video.mp4` exists. Phase 5 calls it first.

## Previews
`channel package-preview` builds all five formats from data alone (no script, no render) into
`out/demo/phase4/previews/<format>/`. They are marked `preview` and can never be uploaded; they exist
so you can review every format's title, description and thumbnail.

## Known limits
* The number check pools the fact values of the video's products and band. It does not tie a number to
  the sentence it is in (script claims do). Packaging text is built from templates and hash-gated, so
  this is a backstop, not the primary defence.
* `contains_synthetic_media: true` is set by default (realistic synthetic voice). Whether YouTube
  requires that label for plain TTS narration is your call; change it in the config.
* Shorts: YouTube does not reliably accept custom thumbnails for Shorts through the API, so the
  vertical cover is for the upload folder and promotion; the Short is identified by `#Shorts` and its
  vertical format.
* Title variants are templates, not tested for click-through yet. Phase 6 compares them.
