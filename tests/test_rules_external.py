"""Tests for external finding filtering in the rules engine."""
from aigiscode.rules.engine import filter_external_findings


def test_filter_external_findings_no_rules():
    findings = [{"tool": "ruff", "file_path": "test.py"}]
    result, excluded = filter_external_findings(findings, [])
    assert result == findings
    assert excluded == 0


def test_filter_external_findings_no_findings():
    result, excluded = filter_external_findings([], [])
    assert result == []
    assert excluded == 0


def test_filter_external_findings_passthrough():
    """With rules but no matching logic yet, all findings pass through."""
    from aigiscode.rules.engine import Rule
    rules = [Rule(id="r1", category="security", checks=[], reason="test")]
    findings = [{"tool": "ruff"}]
    result, excluded = filter_external_findings(findings, rules)
    assert result == findings
    assert excluded == 0
