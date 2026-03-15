"""Tests for allocate_archive_stem, archive_stem support in write_reports, and handoff files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from aigiscode.models import GraphAnalysisResult, ReportData
from aigiscode.report.generator import allocate_archive_stem, write_reports


def _minimal_report(generated_at: datetime | None = None) -> ReportData:
    """Return a minimal ReportData for testing."""
    return ReportData(
        project_path="/tmp/test-project",
        generated_at=generated_at or datetime(2026, 3, 15, 10, 0, 0),
        files_indexed=5,
        symbols_extracted=10,
        dependencies_found=3,
        graph_analysis=GraphAnalysisResult(),
    )


# --- allocate_archive_stem ---


def test_allocate_archive_stem_returns_timestamp_when_no_conflict(tmp_path: Path) -> None:
    stem = allocate_archive_stem(tmp_path, "20260315_100000")
    assert stem == "20260315_100000"


def test_allocate_archive_stem_appends_counter_on_conflict(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    (reports_dir / "20260315_100000").mkdir(parents=True)
    stem = allocate_archive_stem(tmp_path, "20260315_100000")
    assert stem == "20260315_100000_1"


def test_allocate_archive_stem_increments_counter_multiple_conflicts(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    (reports_dir / "20260315_100000").mkdir(parents=True)
    (reports_dir / "20260315_100000_1").mkdir(parents=True)
    (reports_dir / "20260315_100000_2").mkdir(parents=True)
    stem = allocate_archive_stem(tmp_path, "20260315_100000")
    assert stem == "20260315_100000_3"


# --- write_reports with archive_stem ---


def test_write_reports_with_archive_stem_creates_subdirectory(tmp_path: Path) -> None:
    report = _minimal_report()
    md_path, json_path = write_reports(report, tmp_path, archive_stem="20260315_100000")

    # Main files still at root
    assert md_path == tmp_path / "aigiscode-report.md"
    assert json_path == tmp_path / "aigiscode-report.json"
    assert md_path.exists()
    assert json_path.exists()

    # Archive files in stem subdirectory
    stem_dir = tmp_path / "reports" / "20260315_100000"
    assert stem_dir.is_dir()
    assert (stem_dir / "aigiscode-report.md").exists()
    assert (stem_dir / "aigiscode-report.json").exists()


def test_write_reports_without_archive_stem_uses_flat_timestamp(tmp_path: Path) -> None:
    generated_at = datetime(2026, 3, 15, 10, 0, 0)
    report = _minimal_report(generated_at=generated_at)
    md_path, json_path = write_reports(report, tmp_path)

    # Flat archive files (no subdirectory)
    assert (tmp_path / "reports" / "20260315_100000-aigiscode-report.md").exists()
    assert (tmp_path / "reports" / "20260315_100000-aigiscode-report.json").exists()


# --- handoff files ---


def test_write_reports_creates_handoff_files(tmp_path: Path) -> None:
    report = _minimal_report()
    write_reports(report, tmp_path)

    handoff_md = tmp_path / "aigiscode-handoff.md"
    handoff_json = tmp_path / "aigiscode-handoff.json"
    assert handoff_md.exists()
    assert handoff_json.exists()

    # Verify JSON contents
    data = json.loads(handoff_json.read_text(encoding="utf-8"))
    assert data["project_path"] == "/tmp/test-project"
    assert data["files_indexed"] == 5
    assert data["symbols_extracted"] == 10
    assert data["circular_dependencies"] == 0
    assert data["god_classes"] == 0
    assert data["layer_violations"] == 0
    assert data["dead_code_total"] == 0
    assert data["hardwiring_total"] == 0

    # Verify markdown contents
    md_text = handoff_md.read_text(encoding="utf-8")
    assert "# AigisCode Handoff" in md_text
    assert "/tmp/test-project" in md_text
    assert "Files: 5" in md_text
