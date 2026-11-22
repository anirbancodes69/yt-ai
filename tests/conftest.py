from __future__ import annotations

from typing import Any

import pytest

from channel.config import REPO_ROOT, Settings, load_settings
from channel.data import Dataset, ManualCSVProvider, Product, load_dataset
from channel.scoring import ScoringConfig, load_scoring_config


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings(REPO_ROOT, env={})


@pytest.fixture(scope="session")
def dataset(settings: Settings) -> Dataset:
    return load_dataset(settings.paths.products, ManualCSVProvider(settings.paths.prices))


@pytest.fixture(scope="session")
def cfg(settings: Settings) -> ScoringConfig:
    return load_scoring_config(settings.paths.scoring)


def product_dict(dataset: Dataset, pid: str = "tarang-t9") -> dict[str, Any]:
    """A mutable copy of a sample product, for building variants in tests."""
    return dataset.product(pid).model_dump(mode="json")


def make_product(
    dataset: Dataset, pid: str, base: str = "tarang-t9", **spec_values: Any
) -> Product:
    d = product_dict(dataset, base)
    d["id"] = pid
    d["model"] = pid
    for k, v in spec_values.items():
        d["specs"][k]["value"] = v
    return Product.model_validate(d)


@pytest.fixture
def tmp_settings(settings: Settings, tmp_path) -> Settings:
    """Real data/config, but out/ and the SQLite DB in a temp dir."""
    paths = settings.paths.model_copy(
        update={"out": tmp_path / "out", "cache": tmp_path / "cache", "db": tmp_path / "db.sqlite"}
    )
    return settings.model_copy(update={"paths": paths})
