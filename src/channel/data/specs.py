"""Registry of allowed spec keys.

Every spec in products.yaml must use a key from this registry with the right
value type. This catches typos ("batery_mah") at load time, and gives later
phases (render, scripts) one place to look up units and localised labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpecType = Literal["int", "float", "bool", "str"]


@dataclass(frozen=True)
class SpecDef:
    key: str
    type: SpecType
    unit: str | None
    label_hi: str
    label_en: str

    def label(self, lang: str) -> str:
        return self.label_hi if lang == "hi" else self.label_en


_DEFS: tuple[SpecDef, ...] = (
    SpecDef("chipset", "str", None, "प्रोसेसर", "Chipset"),
    SpecDef("antutu_v10", "int", "points", "AnTuTu v10 स्कोर", "AnTuTu v10 score"),
    SpecDef("ram_gb", "int", "GB", "रैम", "RAM"),
    SpecDef("storage_gb", "int", "GB", "स्टोरेज", "Storage"),
    SpecDef("display_size_in", "float", "inch", "डिस्प्ले साइज़", "Display size"),
    SpecDef("display_type", "str", None, "डिस्प्ले टाइप", "Display type"),
    SpecDef("refresh_rate_hz", "int", "Hz", "रिफ्रेश रेट", "Refresh rate"),
    SpecDef("peak_brightness_nits", "int", "nits", "पीक ब्राइटनेस", "Peak brightness"),
    SpecDef("main_camera_mp", "int", "MP", "मेन कैमरा", "Main camera"),
    SpecDef("main_camera_ois", "bool", None, "OIS", "OIS"),
    SpecDef("ultrawide_mp", "int", "MP", "अल्ट्रावाइड कैमरा", "Ultrawide camera"),
    SpecDef("front_camera_mp", "int", "MP", "फ्रंट कैमरा", "Front camera"),
    SpecDef("battery_mah", "int", "mAh", "बैटरी", "Battery"),
    SpecDef("charging_w", "int", "W", "चार्जिंग", "Charging"),
    SpecDef("weight_g", "int", "g", "वज़न", "Weight"),
    SpecDef("ip_rating", "str", None, "IP रेटिंग", "IP rating"),
    SpecDef("os_updates_years", "int", "years", "OS अपडेट", "OS updates"),
    SpecDef("security_updates_years", "int", "years", "सिक्योरिटी अपडेट", "Security updates"),
    SpecDef("has_5g", "bool", None, "5G", "5G"),
)

SPEC_DEFS: dict[str, SpecDef] = {d.key: d for d in _DEFS}


def check_value_type(key: str, value: object) -> None:
    """Raise ValueError if ``value`` does not match the registered type for ``key``."""
    if key not in SPEC_DEFS:
        raise ValueError(f"unknown spec key {key!r}; add it to channel.data.specs first")
    expected = SPEC_DEFS[key].type
    # bool is a subclass of int in Python, so check it explicitly first.
    ok = {
        "bool": isinstance(value, bool),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "float": isinstance(value, (int, float)) and not isinstance(value, bool),
        "str": isinstance(value, str),
    }[expected]
    if not ok:
        raise ValueError(f"spec {key!r} expects {expected}, got {type(value).__name__} ({value!r})")
