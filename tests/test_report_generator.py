from __future__ import annotations

from codexaudit.models import GraphAnalysisResult, ReportData
from codexaudit.report.generator import generate_json_report, generate_markdown_report


def test_report_includes_detector_coverage_warning() -> None:
    report = ReportData(
        project_path="/tmp/project",
        files_indexed=10,
        symbols_extracted=20,
        dependencies_found=5,
        language_breakdown={"php": 5, "python": 5},
        detector_coverage={"dead_code": ["python"], "hardwiring": ["python"]},
        graph_analysis=GraphAnalysisResult(),
    )

    markdown = generate_markdown_report(report)
    payload = generate_json_report(report)

    assert "Detector partial coverage" in markdown
    assert "dead_code | python" in markdown
    assert payload["summary"]["detector_coverage"] == {
        "dead_code": ["python"],
        "hardwiring": ["python"],
    }
