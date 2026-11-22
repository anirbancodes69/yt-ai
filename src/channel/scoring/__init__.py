"""Config-driven, deterministic, explainable product scoring."""

from channel.scoring.config import ScoringConfig, load_scoring_config
from channel.scoring.engine import (
    BandRanking,
    CategoryScore,
    Comparison,
    MetricScore,
    ProductScore,
    RankedEntry,
    ScoringError,
    compare,
    data_ref,
    rank_band,
    score_product,
)

__all__ = [
    "BandRanking",
    "CategoryScore",
    "Comparison",
    "MetricScore",
    "ProductScore",
    "RankedEntry",
    "ScoringConfig",
    "ScoringError",
    "compare",
    "data_ref",
    "load_scoring_config",
    "rank_band",
    "score_product",
]
