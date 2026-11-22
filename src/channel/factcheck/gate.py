"""The publishing gate. Render and upload call ``require_passed`` first."""

from __future__ import annotations

from pathlib import Path

from channel.factcheck.checker import FactCheckReport, data_fingerprint, sha256_text
from channel.scripts.models import Script


class FactCheckBlocked(RuntimeError):
    pass


def require_passed(video_dir: Path, data_files: tuple[Path, ...]) -> Script:
    """Return the script only if it has a passing report for this exact script
    and the current data. Otherwise raise FactCheckBlocked."""
    script_path, report_path = video_dir / "script.json", video_dir / "factcheck.json"
    if not script_path.exists():
        raise FactCheckBlocked(f"{script_path} not found")
    if not report_path.exists():
        raise FactCheckBlocked(f"{video_dir.name}: no fact-check report; run `channel factcheck`")
    script = Script.model_validate_json(script_path.read_text(encoding="utf-8"))
    report = FactCheckReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    if report.status != "pass":
        raise FactCheckBlocked(f"{video_dir.name}: fact-check FAILED ({len(report.errors)} errors)")
    if sha256_text(script.canonical_json()) != report.script_sha256:
        raise FactCheckBlocked(f"{video_dir.name}: script changed after it was fact-checked")
    if data_fingerprint(data_files) != report.data_fingerprint:
        raise FactCheckBlocked(f"{video_dir.name}: data changed after fact-check; re-run it")
    return script
