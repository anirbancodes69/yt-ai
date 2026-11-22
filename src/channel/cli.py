"""Command-line entry point: ``uv run channel <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from channel.config import Settings, load_settings
from channel.data import DataError, Dataset, PriceProviderError, load_dataset, make_provider
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

SAMPLE_BANNER = "*** SAMPLE DATA — fictional products and invented prices, not real ***"


def _context(settings: Settings) -> tuple[Dataset, ScoringConfig]:
    provider = make_provider(settings.price_provider, csv_path=settings.paths.prices)
    dataset = load_dataset(settings.paths.products, provider)
    cfg = load_scoring_config(settings.paths.scoring)
    return dataset, cfg


def _bands(settings: Settings) -> list[int]:
    raw = yaml.safe_load(settings.paths.formats.read_text(encoding="utf-8"))
    return [int(b) for b in raw["price_bands_inr"]]


def _as_of(args: argparse.Namespace, settings: Settings, dataset: Dataset) -> date:
    if args.as_of:
        return date.fromisoformat(args.as_of)
    # SAMPLE data is frozen in time, so pin it to the demo date by default.
    return settings.demo.as_of if dataset.is_sample else date.today()


def _inr(n: int) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join([*groups, tail])


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


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    """Phase 1 demo: validate, rank every band, one head-to-head; write JSON to out/demo/."""
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

    d = sub.add_parser("demo", help="run the current phase's end-to-end demo")
    d.add_argument("--as-of")
    return p


COMMANDS = {
    "validate": cmd_validate,
    "score": cmd_score,
    "rank": cmd_rank,
    "compare": cmd_compare,
    "demo": cmd_demo,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        return COMMANDS[args.command](args, settings)
    except (DataError, PriceProviderError, ScoringError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
