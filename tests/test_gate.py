from __future__ import annotations

import json
import shutil

import pytest

from channel.factcheck import FactCheckBlocked, require_passed
from channel.scripts.pipeline import Context, generate


def _make(settings):
    ctx = Context.load(settings)
    brief = ctx.brief("deal_short", {"product": "ostra-o2"}, settings.demo.as_of, "hi")
    _s, _r, d = generate(ctx, brief, writer="template")
    return d


def test_gate_passes_for_checked_script(tmp_settings) -> None:
    d = _make(tmp_settings)
    assert require_passed(d, tmp_settings.data_files()).video_id == d.name


def test_gate_blocks_edited_script(tmp_settings) -> None:
    d = _make(tmp_settings)
    data = json.loads((d / "script.json").read_text())
    data["sections"][-1]["narration"] += " 5 स्टार डील!"
    (d / "script.json").write_text(json.dumps(data, ensure_ascii=False))
    with pytest.raises(FactCheckBlocked, match="changed after"):
        require_passed(d, tmp_settings.data_files())


def test_gate_blocks_failed_report(tmp_settings) -> None:
    d = _make(tmp_settings)
    rep = json.loads((d / "factcheck.json").read_text())
    rep["status"] = "fail"
    (d / "factcheck.json").write_text(json.dumps(rep))
    with pytest.raises(FactCheckBlocked, match="FAILED"):
        require_passed(d, tmp_settings.data_files())


def test_gate_blocks_missing_report(tmp_settings) -> None:
    d = _make(tmp_settings)
    (d / "factcheck.json").unlink()
    with pytest.raises(FactCheckBlocked, match="no fact-check report"):
        require_passed(d, tmp_settings.data_files())


def test_gate_blocks_after_data_change(tmp_settings, tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(tmp_settings.paths.prices, data_dir / "prices.csv")
    paths = tmp_settings.paths.model_copy(update={"prices": data_dir / "prices.csv"})
    settings = tmp_settings.model_copy(update={"paths": paths})
    d = _make(settings)
    with (data_dir / "prices.csv").open("a") as fh:
        fh.write(
            "ostra-o2,sample-store-c,18999,24999,2026-11-20,"
            "https://example.com/sample/c/ostra-o2,2026-11-20T10:00:00+05:30,true\n"
        )
    with pytest.raises(FactCheckBlocked, match="data changed"):
        require_passed(d, settings.data_files())
