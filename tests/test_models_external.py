"""Tests for ExternalFinding, ExternalToolRun, ExternalAnalysisResult, and FeedbackLoop models."""

from aigiscode.models import (
    ExternalAnalysisResult,
    ExternalFinding,
    ExternalToolRun,
    FeedbackLoop,
    ReportData,
)


class TestExternalFinding:
    def test_instantiate_with_required_only(self):
        f = ExternalFinding(tool="semgrep")
        assert f.tool == "semgrep"
        assert f.rule_id == ""
        assert f.file_path == ""
        assert f.line == 0
        assert f.message == ""
        assert f.severity == "medium"
        assert f.domain == "security"
        assert f.category == ""
        assert f.fingerprint == ""
        assert f.metadata == {}

    def test_instantiate_with_all_fields(self):
        f = ExternalFinding(
            tool="bandit",
            rule_id="B101",
            file_path="app.py",
            line=42,
            message="Use of assert",
            severity="high",
            domain="security",
            category="assert",
            fingerprint="abc123",
            metadata={"cwe": "CWE-703"},
        )
        assert f.tool == "bandit"
        assert f.rule_id == "B101"
        assert f.metadata == {"cwe": "CWE-703"}


class TestExternalToolRun:
    def test_instantiate_with_required_only(self):
        r = ExternalToolRun(tool="semgrep")
        assert r.tool == "semgrep"
        assert r.command == []
        assert r.status == "pending"
        assert r.findings_count == 0
        assert r.summary == {}
        assert r.version == ""

    def test_instantiate_with_all_fields(self):
        r = ExternalToolRun(
            tool="semgrep",
            command=["semgrep", "--json"],
            status="success",
            findings_count=5,
            summary={"errors": 0},
            version="1.0.0",
        )
        assert r.command == ["semgrep", "--json"]
        assert r.findings_count == 5


class TestExternalAnalysisResult:
    def test_instantiate_defaults(self):
        result = ExternalAnalysisResult()
        assert result.tool_runs == []
        assert result.findings == []

    def test_instantiate_with_data(self):
        run = ExternalToolRun(tool="semgrep")
        finding = ExternalFinding(tool="semgrep")
        result = ExternalAnalysisResult(tool_runs=[run], findings=[finding])
        assert len(result.tool_runs) == 1
        assert len(result.findings) == 1


class TestFeedbackLoop:
    def test_instantiate_defaults(self):
        fl = FeedbackLoop()
        assert fl.detected_total == 0
        assert fl.actionable_visible == 0
        assert fl.accepted_by_policy == 0
        assert fl.rules_generated == 0

    def test_instantiate_with_values(self):
        fl = FeedbackLoop(
            detected_total=100,
            actionable_visible=50,
            accepted_by_policy=30,
            rules_generated=5,
        )
        assert fl.detected_total == 100
        assert fl.rules_generated == 5


class TestReportDataFeedbackLoop:
    def test_report_data_has_feedback_loop(self):
        rd = ReportData(project_path="/tmp/test")
        assert isinstance(rd.feedback_loop, FeedbackLoop)
        assert rd.feedback_loop.detected_total == 0

    def test_report_data_with_custom_feedback_loop(self):
        fl = FeedbackLoop(detected_total=10, rules_generated=2)
        rd = ReportData(project_path="/tmp/test", feedback_loop=fl)
        assert rd.feedback_loop.detected_total == 10
        assert rd.feedback_loop.rules_generated == 2
