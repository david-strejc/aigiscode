"""Tests for the security finding reviewer."""
import asyncio
from unittest.mock import AsyncMock, patch

from aigiscode.models import (
    ExternalAnalysisResult,
    ExternalFinding,
    ExternalToolRun,
    ReviewResult,
)
from aigiscode.review.security_reviewer import review_external_security_findings


def test_review_empty_findings():
    analysis = ExternalAnalysisResult()
    result, rules = asyncio.run(
        review_external_security_findings(
            analysis,
            project_path="/tmp/test",
            project_type="test project",
            review_model="test-model",
            primary_backend="codex",
            allow_claude_fallback=False,
        )
    )
    assert isinstance(result, ReviewResult)
    assert result.total_reviewed == 0
    assert rules == []


@patch("aigiscode.review.security_reviewer.generate_text")
def test_review_with_findings_no_backend(mock_gen):
    mock_gen.return_value = (None, "none")
    analysis = ExternalAnalysisResult(
        findings=[
            ExternalFinding(
                tool="ruff",
                rule_id="S101",
                file_path="app/main.py",
                line=10,
                message="Use of assert",
                domain="security",
            )
        ],
        tool_runs=[ExternalToolRun(tool="ruff", command=["ruff"], status="success")],
    )
    result, rules = asyncio.run(
        review_external_security_findings(
            analysis,
            project_path="/tmp/test",
            project_type="test project",
            review_model="test-model",
            primary_backend="codex",
            allow_claude_fallback=False,
        )
    )
    assert isinstance(result, ReviewResult)
    assert result.total_reviewed == 1
    assert result.needs_context == 1


@patch("aigiscode.review.security_reviewer.generate_text")
def test_review_parses_ai_response(mock_gen):
    mock_gen.return_value = ('{"verdicts": [{"index": 0, "verdict": "true_positive", "reason": "real issue"}]}', "codex_sdk")
    analysis = ExternalAnalysisResult(
        findings=[
            ExternalFinding(tool="ruff", rule_id="S101", file_path="app/main.py", line=10, message="assert", domain="security")
        ],
    )
    result, rules = asyncio.run(
        review_external_security_findings(
            analysis, project_path="/tmp/test",
        )
    )
    assert result.true_positives == 1
    assert result.actionable == 1
