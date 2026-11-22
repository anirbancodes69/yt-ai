"""Regenerate the SAMPLE dataset (data/products.yaml, data/prices.csv).

Every brand here is FICTIONAL and every number is INVENTED for development.
All records are flagged sample: true and point at example.com, which the data
models enforce. Run:  uv run python tools/gen_sample_data.py
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TZ = "+05:30"
SPEC_FETCHED = f"2026-11-18T10:00:00{TZ}"
END = date(2026, 11, 20)
WEEKS = 13
RETAILERS = {"sample-store-a": 0, "sample-store-b": 300}

UNITS = {
    "antutu_v10": "points",
    "ram_gb": "GB",
    "storage_gb": "GB",
    "display_size_in": "inch",
    "refresh_rate_hz": "Hz",
    "peak_brightness_nits": "nits",
    "main_camera_mp": "MP",
    "ultrawide_mp": "MP",
    "front_camera_mp": "MP",
    "battery_mah": "mAh",
    "charging_w": "W",
    "weight_g": "g",
    "os_updates_years": "years",
    "security_updates_years": "years",
}

# (id, brand, model, launched_on, launch_price, current_price, drop_on, specs)
PHONES = [
    (
        "zentra-z5",
        "Zentra",
        "Z5",
        "2026-03-10",
        11999,
        9499,
        None,
        dict(
            chipset="SAMPLE-Chip D6100",
            antutu_v10=420000,
            ram_gb=4,
            storage_gb=64,
            display_size_in=6.6,
            display_type="lcd",
            refresh_rate_hz=90,
            peak_brightness_nits=600,
            main_camera_mp=50,
            main_camera_ois=False,
            ultrawide_mp=0,
            front_camera_mp=8,
            battery_mah=5000,
            charging_w=18,
            weight_g=192,
            ip_rating="ip54",
            os_updates_years=2,
            security_updates_years=3,
            has_5g=True,
        ),
    ),
    (
        "kovix-k10",
        "Kovix",
        "K10 5G",
        "2026-05-02",
        12499,
        9999,
        None,
        dict(
            chipset="SAMPLE-Chip D6300",
            antutu_v10=480000,
            ram_gb=6,
            storage_gb=128,
            display_size_in=6.7,
            display_type="lcd",
            refresh_rate_hz=120,
            peak_brightness_nits=650,
            main_camera_mp=50,
            main_camera_ois=False,
            ultrawide_mp=0,
            front_camera_mp=8,
            battery_mah=5000,
            charging_w=33,
            weight_g=199,
            ip_rating="none",
            os_updates_years=1,
            security_updates_years=2,
            has_5g=True,
        ),
    ),
    (
        "lumora-l3",
        "Lumora",
        "L3",
        "2026-04-15",
        16499,
        13999,
        None,
        dict(
            chipset="SAMPLE-Chip S4G2",
            antutu_v10=560000,
            ram_gb=6,
            storage_gb=128,
            display_size_in=6.67,
            display_type="amoled",
            refresh_rate_hz=120,
            peak_brightness_nits=1000,
            main_camera_mp=50,
            main_camera_ois=False,
            ultrawide_mp=8,
            front_camera_mp=16,
            battery_mah=5000,
            charging_w=33,
            weight_g=188,
            ip_rating="ip54",
            os_updates_years=2,
            security_updates_years=4,
            has_5g=True,
        ),
    ),
    (
        "pravah-p7",
        "Pravah",
        "P7",
        "2026-06-20",
        17499,
        14999,
        None,
        dict(
            chipset="SAMPLE-Chip D7025",
            antutu_v10=640000,
            ram_gb=8,
            storage_gb=128,
            display_size_in=6.72,
            display_type="lcd",
            refresh_rate_hz=120,
            peak_brightness_nits=700,
            main_camera_mp=64,
            main_camera_ois=False,
            ultrawide_mp=8,
            front_camera_mp=16,
            battery_mah=6000,
            charging_w=45,
            weight_g=205,
            ip_rating="ip53",
            os_updates_years=2,
            security_updates_years=3,
            has_5g=True,
        ),
    ),
    (
        "tarang-t9",
        "Tarang",
        "T9",
        "2026-07-08",
        21999,
        18999,
        None,
        dict(
            chipset="SAMPLE-Chip S7G3",
            antutu_v10=720000,
            ram_gb=8,
            storage_gb=256,
            display_size_in=6.67,
            display_type="amoled",
            refresh_rate_hz=120,
            peak_brightness_nits=1600,
            main_camera_mp=50,
            main_camera_ois=True,
            ultrawide_mp=8,
            front_camera_mp=20,
            battery_mah=5000,
            charging_w=67,
            weight_g=185,
            ip_rating="ip64",
            os_updates_years=3,
            security_updates_years=4,
            has_5g=True,
        ),
    ),
    # Price drop on 2026-11-06 from 22,999 to 19,999: the deal-Short example.
    (
        "ostra-o2",
        "Ostra",
        "O2",
        "2026-02-25",
        24999,
        19999,
        "2026-11-06",
        dict(
            chipset="SAMPLE-Chip D7300",
            antutu_v10=800000,
            ram_gb=8,
            storage_gb=128,
            display_size_in=6.6,
            display_type="amoled",
            refresh_rate_hz=120,
            peak_brightness_nits=1200,
            main_camera_mp=108,
            main_camera_ois=False,
            ultrawide_mp=8,
            front_camera_mp=16,
            battery_mah=5500,
            charging_w=45,
            weight_g=195,
            ip_rating="ip54",
            os_updates_years=2,
            security_updates_years=3,
            has_5g=True,
        ),
    ),
    (
        "zentra-z8-pro",
        "Zentra",
        "Z8 Pro",
        "2026-10-15",
        29999,
        27999,
        None,
        dict(
            chipset="SAMPLE-Chip S8S3",
            antutu_v10=1050000,
            ram_gb=12,
            storage_gb=256,
            display_size_in=6.7,
            display_type="amoled",
            refresh_rate_hz=120,
            peak_brightness_nits=1800,
            main_camera_mp=50,
            main_camera_ois=True,
            ultrawide_mp=12,
            front_camera_mp=32,
            battery_mah=5000,
            charging_w=80,
            weight_g=190,
            ip_rating="ip65",
            os_updates_years=4,
            security_updates_years=5,
            has_5g=True,
        ),
    ),
    (
        "lumora-l9-ultra",
        "Lumora",
        "L9 Ultra",
        "2026-01-20",
        49999,
        44999,
        None,
        dict(
            chipset="SAMPLE-Chip S8G4",
            antutu_v10=1450000,
            ram_gb=12,
            storage_gb=256,
            display_size_in=6.8,
            display_type="amoled",
            refresh_rate_hz=144,
            peak_brightness_nits=2000,
            main_camera_mp=50,
            main_camera_ois=True,
            ultrawide_mp=12,
            front_camera_mp=32,
            battery_mah=5500,
            charging_w=65,
            weight_g=205,
            ip_rating="ip68",
            os_updates_years=5,
            security_updates_years=6,
            has_5g=True,
        ),
    ),
]


def products_yaml() -> str:
    products = []
    for pid, brand, model, launched, launch_price, _cur, _drop, specs in PHONES:
        url = f"https://example.com/sample/{pid}"
        products.append(
            {
                "id": pid,
                "brand": brand,
                "model": model,
                "category": "phone",
                "sample": True,
                "launch": {
                    "launched_on": launched,
                    "price_inr": launch_price,
                    "source_url": f"{url}/launch",
                    "fetched_at": SPEC_FETCHED,
                },
                "specs": {
                    k: {
                        "value": v,
                        **({"unit": UNITS[k]} if k in UNITS else {}),
                        "source_url": f"{url}/specs",
                        "fetched_at": SPEC_FETCHED,
                    }
                    for k, v in specs.items()
                },
                "press_images": [],
                "listings": [
                    {
                        "retailer": retailer,
                        "url": f"https://example.com/sample/{retailer}/{pid}",
                        "source_url": f"https://example.com/sample/{retailer}/{pid}",
                        "fetched_at": SPEC_FETCHED,
                    }
                    for retailer in RETAILERS
                ],
            }
        )
    header = (
        "# SAMPLE DATA — fictional brands, invented numbers. NOT real products.\n"
        "# Generated by tools/gen_sample_data.py; edit that file, not this one.\n"
    )
    return header + yaml.safe_dump(
        {"products": products}, sort_keys=False, allow_unicode=True, width=100
    )


def price_rows() -> list[dict[str, object]]:
    rows = []
    for pid, _b, _m, launched, launch_price, current, drop_on, _s in PHONES:
        launched_d = date.fromisoformat(launched)
        drop_d = date.fromisoformat(drop_on) if drop_on else None
        for k in range(WEEKS, -1, -1):  # k weeks before END
            day = END - timedelta(weeks=k)
            if day < launched_d:
                continue
            if drop_d is not None:
                base = current if day >= drop_d else current + 3000
            else:
                base = current + 500 * min(k, 4)  # gentle decline to today's price
            for retailer, delta in RETAILERS.items():
                rows.append(
                    {
                        "product_id": pid,
                        "retailer": retailer,
                        "price_inr": min(base + delta, launch_price),
                        "mrp_inr": launch_price,
                        "observed_on": day.isoformat(),
                        "source_url": f"https://example.com/sample/{retailer}/{pid}",
                        "fetched_at": f"{day.isoformat()}T10:00:00{TZ}",
                        "sample": "true",
                    }
                )
    return rows


def main() -> None:
    (ROOT / "data" / "products.yaml").write_text(products_yaml(), encoding="utf-8")
    rows = price_rows()
    with (ROOT / "data" / "prices.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(PHONES)} products, {len(rows)} price rows (SAMPLE)")


if __name__ == "__main__":
    main()
