"""Number and date formatting shared by scripts, fact-check and (later) render."""

from __future__ import annotations

from datetime import date


def group_indian(n: int) -> str:
    """1234567 -> '12,34,567' (Indian digit grouping)."""
    sign, s = ("-", str(-n)) if n < 0 else ("", str(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join([*groups, tail])


def inr(n: int) -> str:
    return "₹" + group_indian(n)


def num(v: int | float) -> str:
    """Spoken/displayed form of a number: ints >= 1000 grouped, floats trimmed."""
    if isinstance(v, bool):
        raise TypeError("bool is not a number here")
    if isinstance(v, int):
        return group_indian(v) if abs(v) >= 1000 else str(v)
    if float(v).is_integer():
        return num(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def long_date(d: date, months: list[str]) -> str:
    return f"{d.day} {months[d.month - 1]} {d.year}"
