from __future__ import annotations

import json
from pathlib import Path

import pytest

from channel import cli
from channel.config import REPO_ROOT, load_settings, resolve_lang


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "hi"),
        ({"LANG": "en_US.UTF-8"}, "hi"),  # shell locale is not a channel language
        ({"LANG": "en"}, "en"),
        ({"LANG": "en", "CHANNEL_LANG": "hi"}, "hi"),
        ({"CHANNEL_LANG": "en"}, "en"),
    ],
)
def test_resolve_lang(env, expected) -> None:
    assert resolve_lang(env, "hi") == expected


def test_bad_channel_lang() -> None:
    with pytest.raises(ValueError, match="CHANNEL_LANG"):
        resolve_lang({"CHANNEL_LANG": "fr"}, "hi")


def test_settings_paths_are_absolute() -> None:
    s = load_settings(REPO_ROOT, env={"CHANNEL_LLM_MODEL": "claude-haiku-5-5"})
    assert s.paths.products.is_absolute() and s.paths.products.exists()
    assert s.llm_model == "claude-haiku-5-5"
    assert s.lang == "hi"


@pytest.mark.parametrize(
    ("n", "out"), [(999, "₹999"), (9999, "₹9,999"), (149999, "₹1,49,999"), (1234567, "₹12,34,567")]
)
def test_inr_grouping(n, out) -> None:
    assert cli._inr(n) == out


@pytest.fixture
def tmp_out(monkeypatch, tmp_path: Path):
    real = load_settings(REPO_ROOT, env={})
    patched = real.model_copy(update={"paths": real.paths.model_copy(update={"out": tmp_path})})
    monkeypatch.setattr(cli, "load_settings", lambda: patched)
    return tmp_path


def test_cli_validate(tmp_out, capsys) -> None:
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "SAMPLE DATA" in out and "8 products" in out


def test_cli_rank_json(tmp_out, capsys) -> None:
    assert cli.main(["rank", "--band", "20000", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["entries"][0]["product_id"] == "tarang-t9"
    assert data["sample"] is True


def test_cli_unknown_product_is_clean_error(tmp_out, capsys) -> None:
    assert cli.main(["score", "nope"]) == 2
    assert "unknown product" in capsys.readouterr().err


def test_cli_demo_writes_outputs(tmp_out, capsys) -> None:
    assert cli.main(["demo"]) == 0
    files = {p.name for p in (tmp_out / "demo" / "phase1").iterdir()}
    assert {f"best_under_{b}.json" for b in (10000, 15000, 20000, 30000, 50000)} <= files
    assert "scores.json" in files
