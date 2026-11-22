"""Fact-check scripts against the data; block publishing on any mismatch."""

from channel.factcheck.checker import (
    FactCheckConfig,
    FactChecker,
    FactCheckReport,
    Issue,
    data_fingerprint,
    extract_numbers,
    load_factcheck_config,
)
from channel.factcheck.gate import FactCheckBlocked, require_passed

__all__ = [
    "FactCheckBlocked",
    "FactCheckConfig",
    "FactCheckReport",
    "FactChecker",
    "Issue",
    "data_fingerprint",
    "extract_numbers",
    "load_factcheck_config",
    "require_passed",
]
