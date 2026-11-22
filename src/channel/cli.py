"""Command-line entry point: ``uv run channel <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from channel.config import Settings, load_settings
from channel.data import DataError, Dataset, PriceProviderError, load_dataset, make_provider
from channel.factcheck import FactCheckBlocked, require_passed
from channel.fmt import inr, num
from channel.formats import FORMAT_NAMES, load_formats
from channel.render.fonts import font_set
from channel.render.pipeline import contact_sheet, render_video
from channel.render.testframe import render_caption_test, render_test_frame
from channel.scoring import (
    BandRanking,
    Comparison,
    ProductScore,
    ScoringConfig,
    ScoringError,
    compare,
    load_scoring_config,
    rank_band,
    score_product,
)
from channel.scripts.briefs import BriefError
from channel.scripts.llm import AnthropicScriptLLM, CachedLLM, ScriptGenerationError
from channel.scripts.models import Script
from channel.scripts.pipeline import Context, FactCheckFailed, fact_check, generate, recheck
from channel.scripts.templates import TemplateUnsupported
from channel.store import Budget, BudgetExceeded, Store

SAMPLE_BANNER = "*** SAMPLE DATA — fictional products and invented prices, not real ***"


def _context(settings: Settings) -> tuple[Dataset, ScoringConfig]:
    provider = make_provider(settings.price_provider, csv_path=settings.paths.prices)
    dataset = load_dataset(settings.paths.products, provider)
    cfg = load_scoring_config(settings.paths.scoring)
    return dataset, cfg


def _bands(settings: Settings) -> list[int]:
    return list(load_formats(settings.paths.formats).price_bands_inr)


def _as_of(args: argparse.Namespace, settings: Settings, dataset: Dataset) -> date:
    if args.as_of:
        return date.fromisoformat(args.as_of)
    # SAMPLE data is frozen in time, so pin it to the demo date by default.
    return settings.demo.as_of if dataset.is_sample else date.today()


_inr = inr


# ---------------------------------------------------------------------------- printing


def print_score(s: ProductScore) -> None:
    print(f"{s.display_name}  overall {s.overall:.2f}/10  (scoring v{s.scoring_version})")
    for c in s.categories:
        print(f"  {c.name:<12} {c.score:5.2f} × {c.weight:.2f} = {c.contribution:.2f}")
        for m in c.metrics:
            print(f"      {m.spec:<24} {m.raw_value!s:<18} → {m.normalized:.2f} × {m.weight:.2f}")
    print(f"  strengths: {', '.join(s.strengths) or '-'}")
    print(f"  weaknesses: {', '.join(s.weaknesses) or '-'}")


def print_ranking(r: BandRanking) -> None:
    print(f"Best under {_inr(r.band_inr)} — price as of {r.as_of.isoformat()}")
    if not r.entries:
        print("  (no products in this band)")
    for e in r.entries:
        print(
            f"  #{e.rank} {e.display_name:<28} {e.overall:5.2f}/10  "
            f"{_inr(e.price.price_inr):>9} @ {e.price.retailer} ({e.price.observed_on})"
        )
    for x in r.excluded:
        print(f"     excluded {x.product_id}: {x.reason}")


def print_comparison(c: Comparison) -> None:
    print(f"{c.a.display_name}  vs  {c.b.display_name}   (tie if diff < {c.tie_margin})")
    for v in c.categories:
        w = v.winner or "tie"
        print(f"  {v.name:<12} {v.a_score:5.2f}  {v.b_score:5.2f}   → {w}")
    print(f"  overall      {c.a.overall:5.2f}  {c.b.overall:5.2f}   → {c.overall_winner or 'tie'}")


# ---------------------------------------------------------------------------- commands


def cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    dataset, cfg = _context(settings)
    for p in dataset.products.values():
        score_product(p, cfg)  # proves every product has every spec scoring needs
    if dataset.is_sample:
        print(SAMPLE_BANNER)
    print(
        f"OK: {len(dataset.products)} products, {len(dataset.prices)} price rows, "
        f"scoring v{cfg.version} ({cfg.fingerprint})"
    )
    return 0


def cmd_score(args: argparse.Namespace, settings: Settings) -> int:
    dataset, cfg = _context(settings)
    s = score_product(dataset.product(args.product_id), cfg, lang=settings.lang)
    if args.json:
        print(s.model_dump_json(indent=2))
    else:
        print_score(s)
    return 0


def cmd_rank(args: argparse.Namespace, settings: Settings) -> int:
    dataset, cfg = _context(settings)
    r = rank_band(
        dataset,
        cfg,
        band_inr=args.band,
        as_of=_as_of(args, settings, dataset),
        max_price_age_days=settings.max_price_age_days,
        lang=settings.lang,
    )
    if args.json:
        print(r.model_dump_json(indent=2))
    else:
        if r.sample:
            print(SAMPLE_BANNER)
        print_ranking(r)
    return 0


def cmd_compare(args: argparse.Namespace, settings: Settings) -> int:
    dataset, cfg = _context(settings)
    c = compare(dataset.product(args.a), dataset.product(args.b), cfg, lang=settings.lang)
    if args.json:
        print(c.model_dump_json(indent=2))
    else:
        if dataset.is_sample:
            print(SAMPLE_BANNER)
        print_comparison(c)
    return 0


def cmd_rankings(args: argparse.Namespace, settings: Settings) -> int:
    """Rank every band and one head-to-head; write JSON to out/demo/phase1/."""
    dataset, cfg = _context(settings)
    as_of = _as_of(args, settings, dataset)
    out = Path(settings.paths.out) / "demo" / "phase1"
    out.mkdir(parents=True, exist_ok=True)
    if dataset.is_sample:
        print(SAMPLE_BANNER)
    print(f"lang={settings.lang} as_of={as_of} scoring v{cfg.version} ({cfg.fingerprint})\n")

    for band in _bands(settings):
        r = rank_band(
            dataset,
            cfg,
            band_inr=band,
            as_of=as_of,
            max_price_age_days=settings.max_price_age_days,
            lang=settings.lang,
        )
        (out / f"best_under_{band}.json").write_text(r.model_dump_json(indent=2), "utf-8")
        print_ranking(r)
        print()

    c = compare(dataset.product("tarang-t9"), dataset.product("ostra-o2"), cfg, lang=settings.lang)
    (out / "compare_tarang-t9_vs_ostra-o2.json").write_text(c.model_dump_json(indent=2), "utf-8")
    print_comparison(c)

    scores = {
        pid: json.loads(score_product(p, cfg, lang=settings.lang).model_dump_json())
        for pid, p in sorted(dataset.products.items())
    }
    (out / "scores.json").write_text(json.dumps(scores, indent=2, ensure_ascii=False), "utf-8")
    print(f"\nwrote {out}/")
    return 0


# ------------------------------------------------------------------- scripts (phase 2)


def _script_params(args: argparse.Namespace) -> dict[str, str | int]:
    need = {
        "best_under": ("band",),
        "monthly_refresh": ("band",),
        "head_to_head": ("a", "b"),
        "launch_explainer": ("product",),
        "deal_short": ("product",),
    }[args.format]
    params: dict[str, str | int] = {}
    for k in need:
        v = getattr(args, k)
        if v is None:
            raise ValueError(f"format {args.format} needs --{k}")
        params[k] = v
    return params


def make_llm(settings: Settings, store: Store) -> CachedLLM:
    return CachedLLM(
        AnthropicScriptLLM(
            model=settings.llm.model, effort=settings.llm.effort, max_tokens=settings.llm.max_tokens
        ),
        store=store,
        budget=Budget(settings.budget.monthly_inr, settings.budget.per_video_inr),
        pricing=settings.pricing_usd_per_mtok,
        usd_to_inr=settings.budget.usd_to_inr,
    )


def cmd_script(args: argparse.Namespace, settings: Settings) -> int:
    ctx = Context.load(settings)
    as_of = _as_of(args, settings, ctx.dataset)
    brief = ctx.brief(args.format, _script_params(args), as_of, args.lang or settings.lang)
    store = Store(settings.paths.db)
    try:
        llm = make_llm(settings, store) if args.writer == "llm" else None
        _script, report, video_dir = generate(ctx, brief, writer=args.writer, llm=llm)
    except FactCheckFailed as exc:
        print(exc.report.summary())
        print(f"BLOCKED — report written to {exc.video_dir}/factcheck.json", file=sys.stderr)
        return 1
    finally:
        spent = store.video_spend_inr(brief.video_id)
        store.close()
    print(report.summary())
    print(f"wrote {video_dir}/  (API cost for this video so far: ₹{spent:.2f})")
    return 0


def cmd_factcheck(args: argparse.Namespace, settings: Settings) -> int:
    ctx = Context.load(settings)
    report, _ = recheck(ctx, Path(args.script))
    print(report.summary())
    return 0 if report.status == "pass" else 1


def cmd_costs(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.paths.db)
    try:
        if args.video:
            for row in store.video_costs(args.video):
                print(
                    f"{row['created_at'][:19]} {row['operation']:<14} {row['model'] or '-':<18} "
                    f"in={row['input_tokens']} out={row['output_tokens']} "
                    f"₹{row['inr']:.2f}{' (cached)' if row['cached'] else ''}"
                )
            print(f"total ₹{store.video_spend_inr(args.video):.2f}")
        else:
            month = args.month
            for row in store.month_by_video(month):
                print(
                    f"{row['video_id']:<48} calls={row['calls']} cached={row['cached_calls']} "
                    f"₹{row['inr']:.2f}"
                )
            total = store.month_spend_inr(month)
            print(f"month total ₹{total:.2f} of ₹{settings.budget.monthly_inr:.0f} budget")
    finally:
        store.close()
    return 0


def _tamper(script: Script) -> Script:
    """Deliberately wrong copy for the demo: a wrong price, an invented number and a
    hands-on claim."""
    data = json.loads(script.model_dump_json())
    for sec in data["sections"]:
        if sec["source"] != "writer" or sec["kind"] != "product":
            continue
        for c in sec["claims"]:
            if c["ref"].endswith(".current"):
                wrong = c["text"].replace(num(c["value"]), num(c["value"] - 1000))
                sec["narration"] = sec["narration"].replace(c["text"], wrong, 1)
                c["text"], c["value"] = wrong, c["value"] - 1000
                break
        sec["narration"] += " हमने टेस्ट किया तो बैटरी 2 दिन चली।"
        break
    return Script.model_validate(data)


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    """Current phase demo: rankings, then template scripts + fact-check + a blocked fake."""
    if cmd_rankings(args, settings) != 0:
        return 1
    print("\n=== Phase 2: scripts + fact-check (template writer, no API key needed) ===")
    ctx = Context.load(settings)
    as_of = _as_of(args, settings, ctx.dataset)
    lang = settings.lang
    made = []
    for fmt, params in (("best_under", {"band": 20000}), ("deal_short", {"product": "ostra-o2"})):
        _s, report, video_dir = generate(
            ctx, ctx.brief(fmt, params, as_of, lang), writer="template"
        )
        print(report.summary())
        require_passed(video_dir, settings.data_files())
        print(f"  gate: OK, render/upload allowed for {video_dir.name}")
        made.append(video_dir)

    print("\n--- deliberately wrong copy (wrong price, invented number, hands-on claim) ---")
    good = Script.model_validate_json((made[0] / "script.json").read_text("utf-8"))
    bad = _tamper(good)
    bad_dir = settings.paths.out / "demo" / "phase2" / "tampered"
    bad_dir.mkdir(parents=True, exist_ok=True)
    report = fact_check(ctx, bad)
    (bad_dir / "script.json").write_text(bad.model_dump_json(indent=2), "utf-8")
    (bad_dir / "factcheck.json").write_text(report.model_dump_json(indent=2), "utf-8")
    print(report.summary())
    try:
        require_passed(bad_dir, settings.data_files())
    except FactCheckBlocked as exc:
        print(f"  gate: BLOCKED as expected — {exc}")
    else:
        print("  gate: tampered script was NOT blocked — this is a bug", file=sys.stderr)
        return 1

    if args.skip_render:
        return 0
    print("\n=== Phase 3: voice + render (espeak-ng offline voice; Google TTS when configured) ===")
    if cmd_proofs(args, settings) != 0:
        return 1
    tts = args.tts or "espeak"
    for video_dir in made:
        r = render_video(ctx, video_dir, tts_provider=tts)
        spec = ctx.formats.spec(
            Script.model_validate_json((video_dir / "script.json").read_text("utf-8")).brief.format
        )
        lo, hi = spec.target_seconds
        ok = lo <= r.duration <= hi
        print(
            f"  {'OK ' if ok else 'BAD'} {r.video}  {r.duration:.1f}s (target {lo:.0f}-{hi:.0f}s)  "
            f"{r.width}x{r.height}  shots={r.shots}  API cost ₹{r.cost_inr:.2f}"
        )
        if not ok:
            return 1
    return 0


# -------------------------------------------------------------------- render (phase 3)


def cmd_render(args: argparse.Namespace, settings: Settings) -> int:
    ctx = Context.load(settings)
    video_dir = Path(args.video)
    if not video_dir.is_absolute() and not video_dir.exists():
        video_dir = ctx.video_dir(args.video)
    r = render_video(
        ctx, video_dir, tts_provider=args.tts or settings.tts.provider, keep_frames=args.keep_frames
    )
    print(
        f"rendered {r.video}  {r.duration:.1f}s  {r.width}x{r.height}  shots={r.shots}  "
        f"API cost ₹{r.cost_inr:.2f}"
    )
    return 0


def cmd_proofs(args: argparse.Namespace, settings: Settings) -> int:
    """Devanagari shaping proofs (Pillow + libass) and the five-format contact sheet."""
    ctx = Context.load(settings)
    out = settings.paths.out / "test_frames"
    widths = render_test_frame(font_set(settings.paths.fonts), out / "devanagari.png")
    shaped = all(a != b for a, b in zip(widths["raqm"], widths["basic"], strict=True))
    caps = render_caption_test(out / "captions_devanagari.png")
    sheet = contact_sheet(
        ctx,
        out / "formats_contact_sheet.png",
        lang=settings.lang,
        as_of=_as_of(args, settings, ctx.dataset),
    )
    print(
        f"Pillow raqm shaping differs from BASIC on every sample: {shaped} -> {out}/devanagari.png"
    )
    print(
        f"libass complex shaping differs from simple: {caps['simple'] != caps['complex']} "
        f"-> {out}/captions_devanagari.png"
    )
    print(f"five formats -> {sheet}")
    return 0 if shaped and caps["simple"] != caps["complex"] else 1


# ---------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="channel", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="load and validate data + scoring config")

    s = sub.add_parser("score", help="explain one product's score")
    s.add_argument("product_id")
    s.add_argument("--json", action="store_true")

    r = sub.add_parser("rank", help="rank products under a price band")
    r.add_argument("--band", type=int, required=True, help="price cap in INR, e.g. 20000")
    r.add_argument("--as-of", help="YYYY-MM-DD (default: today; demo date for SAMPLE data)")
    r.add_argument("--json", action="store_true")

    c = sub.add_parser("compare", help="head-to-head between two products")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--json", action="store_true")

    rk = sub.add_parser("rankings", help="rank every band + one head-to-head (JSON to out/)")
    rk.add_argument("--as-of")

    sc = sub.add_parser("script", help="write + fact-check a script for one video")
    sc.add_argument("format", choices=FORMAT_NAMES)
    sc.add_argument("--band", type=int)
    sc.add_argument("--product")
    sc.add_argument("--a")
    sc.add_argument("--b")
    sc.add_argument("--writer", choices=("llm", "template"), default="llm")
    sc.add_argument("--lang", choices=("hi", "en"))
    sc.add_argument("--as-of")

    fc = sub.add_parser("factcheck", help="re-run the fact-check on a saved script.json")
    fc.add_argument("script")

    co = sub.add_parser("costs", help="API cost ledger")
    co.add_argument("--month", help="YYYY-MM (default: current month)")
    co.add_argument("--video")

    rn = sub.add_parser("render", help="voice + render a fact-checked script to MP4")
    rn.add_argument("video", help="video id (out/videos/<id>) or a path to its folder")
    rn.add_argument("--tts", choices=("google", "espeak"), help="default: settings tts.provider")
    rn.add_argument("--keep-frames", action="store_true")

    pf = sub.add_parser("proofs", help="Devanagari shaping proofs + five-format contact sheet")
    pf.add_argument("--as-of")

    d = sub.add_parser("demo", help="run the current phase's end-to-end demo")
    d.add_argument("--as-of")
    d.add_argument("--tts", choices=("google", "espeak"), help="default: espeak (offline)")
    d.add_argument("--skip-render", action="store_true", help="stop after scripts")
    return p


COMMANDS = {
    "validate": cmd_validate,
    "score": cmd_score,
    "rank": cmd_rank,
    "compare": cmd_compare,
    "rankings": cmd_rankings,
    "script": cmd_script,
    "factcheck": cmd_factcheck,
    "costs": cmd_costs,
    "render": cmd_render,
    "proofs": cmd_proofs,
    "demo": cmd_demo,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        return COMMANDS[args.command](args, settings)
    except (
        DataError,
        PriceProviderError,
        ScoringError,
        BriefError,
        TemplateUnsupported,
        ScriptGenerationError,
        BudgetExceeded,
        FactCheckBlocked,
        KeyError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
