"""Tests for aigiscode.orchestration module."""

from __future__ import annotations

import inspect
from dataclasses import fields as dc_fields
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aigiscode.orchestration import (
    DeterministicResult,
    RuntimeEnvironment,
    build_report_data,
    collect_external_analysis_for_report,
    combine_runtime_plugins,
    resolve_runtime_environment,
    run_deterministic_analysis,
    selected_external_tools,
)


# ---------------------------------------------------------------------------
# selected_external_tools
# ---------------------------------------------------------------------------


class TestSelectedExternalTools:
    """Tests for the selected_external_tools helper."""

    def test_none_input_returns_none(self):
        assert selected_external_tools(None) is None

    def test_empty_list_returns_none(self):
        assert selected_external_tools([]) is None

    def test_explicit_tools_returned_as_is(self):
        result = selected_external_tools(["ruff", "gitleaks"])
        assert result == ["ruff", "gitleaks"]

    def test_all_expands_to_supported_tools(self):
        from aigiscode.security.external import SUPPORTED_SECURITY_TOOLS

        result = selected_external_tools(["all"])
        assert result == list(SUPPORTED_SECURITY_TOOLS)

    def test_ruff_security_flag_adds_ruff(self):
        result = selected_external_tools(None, run_ruff_security=True)
        assert result is not None
        assert "ruff" in result

    def test_ruff_security_flag_no_duplicate_with_explicit_ruff(self):
        result = selected_external_tools(["ruff"], run_ruff_security=True)
        assert result is not None
        assert result.count("ruff") == 1

    def test_ruff_security_flag_appends_to_existing(self):
        result = selected_external_tools(["gitleaks"], run_ruff_security=True)
        assert result is not None
        assert "gitleaks" in result
        assert "ruff" in result


# ---------------------------------------------------------------------------
# combine_runtime_plugins
# ---------------------------------------------------------------------------


class TestCombineRuntimePlugins:
    """Tests for the combine_runtime_plugins helper."""

    def test_empty_inputs(self):
        result = combine_runtime_plugins([], [])
        assert result == []

    def test_no_external_plugins(self):
        result = combine_runtime_plugins(["generic", "laravel"], [])
        assert result == []

    def test_with_external_plugins(self):
        from aigiscode.extensions import ExternalPlugin

        plugin = ExternalPlugin(ref="my_plugin", module=MagicMock(), name="my_plugin")
        result = combine_runtime_plugins(["generic", "module:my_plugin"], [plugin])
        assert len(result) == 1
        assert result[0].name == "my_plugin"

    def test_filters_to_applied_plugins_only(self):
        from aigiscode.extensions import ExternalPlugin

        plugin_a = ExternalPlugin(ref="a_ref", module=MagicMock(), name="a_name")
        plugin_b = ExternalPlugin(ref="b_ref", module=MagicMock(), name="b_name")
        # Only a_ref is in the applied list
        result = combine_runtime_plugins(
            ["generic", "module:a_ref"],
            [plugin_a, plugin_b],
        )
        assert len(result) == 1
        assert result[0].name == "a_name"

    def test_returns_all_when_all_applied(self):
        from aigiscode.extensions import ExternalPlugin

        plugin_a = ExternalPlugin(ref="a_ref", module=MagicMock(), name="a_name")
        plugin_b = ExternalPlugin(ref="b_ref", module=MagicMock(), name="b_name")
        result = combine_runtime_plugins(
            ["generic", "module:a_ref", "module:b_ref"],
            [plugin_a, plugin_b],
        )
        assert len(result) == 2


# ---------------------------------------------------------------------------
# RuntimeEnvironment dataclass
# ---------------------------------------------------------------------------


class TestRuntimeEnvironment:
    """Verify the RuntimeEnvironment dataclass structure."""

    def test_has_required_fields(self):
        field_names = {f.name for f in dc_fields(RuntimeEnvironment)}
        assert "policy" in field_names
        assert "runtime_plugins" in field_names

    def test_can_construct(self):
        from aigiscode.policy.models import AnalysisPolicy

        env = RuntimeEnvironment(
            policy=AnalysisPolicy(),
            runtime_plugins=[],
        )
        assert env.policy is not None
        assert env.runtime_plugins == []


# ---------------------------------------------------------------------------
# DeterministicResult dataclass
# ---------------------------------------------------------------------------


class TestDeterministicResult:
    """Verify the DeterministicResult dataclass structure."""

    def test_has_required_fields(self):
        field_names = {f.name for f in dc_fields(DeterministicResult)}
        assert "graph" in field_names
        assert "graph_result" in field_names
        assert "dead_code_result" in field_names
        assert "hardwiring_result" in field_names
        assert "unsupported_breakdown" in field_names

    def test_can_construct(self):
        result = DeterministicResult(
            graph=MagicMock(),
            graph_result=MagicMock(),
            dead_code_result=MagicMock(),
            hardwiring_result=MagicMock(),
            unsupported_breakdown={},
        )
        assert result.graph is not None
        assert result.unsupported_breakdown == {}


# ---------------------------------------------------------------------------
# resolve_runtime_environment — signature + mock
# ---------------------------------------------------------------------------


class TestResolveRuntimeEnvironment:
    """Test resolve_runtime_environment with mocks."""

    def test_signature(self):
        sig = inspect.signature(resolve_runtime_environment)
        assert "config" in sig.parameters

    @patch("aigiscode.orchestration.load_external_plugins")
    @patch("aigiscode.orchestration.resolve_policy")
    def test_returns_runtime_environment(self, mock_resolve_policy, mock_load_plugins):
        from aigiscode.models import AigisCodeConfig
        from aigiscode.policy.models import AnalysisPolicy

        mock_resolve_policy.return_value = AnalysisPolicy(
            plugins_applied=["generic"],
        )
        mock_load_plugins.return_value = []

        config = AigisCodeConfig(project_path=Path("/tmp/fake"))
        env = resolve_runtime_environment(config)

        assert isinstance(env, RuntimeEnvironment)
        assert env.policy.plugins_applied == ["generic"]
        assert env.runtime_plugins == []


# ---------------------------------------------------------------------------
# run_deterministic_analysis — signature + mock
# ---------------------------------------------------------------------------


class TestRunDeterministicAnalysis:
    """Test run_deterministic_analysis with mocks."""

    def test_signature(self):
        sig = inspect.signature(run_deterministic_analysis)
        params = set(sig.parameters.keys())
        assert "config" in params
        assert "store" in params
        assert "policy" in params
        assert "runtime_plugins" in params

    @patch("aigiscode.orchestration.discover_unsupported_source_files")
    @patch("aigiscode.orchestration.apply_hardwiring_result_plugins")
    @patch("aigiscode.orchestration.apply_dead_code_result_plugins")
    @patch("aigiscode.orchestration.apply_graph_result_plugins")
    @patch("aigiscode.orchestration.analyze_hardwiring")
    @patch("aigiscode.orchestration.analyze_dead_code")
    @patch("aigiscode.orchestration.analyze_graph")
    @patch("aigiscode.orchestration.build_file_graph")
    def test_returns_deterministic_result(
        self,
        mock_build_graph,
        mock_analyze_graph,
        mock_dead_code,
        mock_hardwiring,
        mock_apply_graph,
        mock_apply_dead,
        mock_apply_hw,
        mock_unsupported,
    ):
        from aigiscode.models import AigisCodeConfig, GraphAnalysisResult
        from aigiscode.policy.models import AnalysisPolicy

        mock_graph = MagicMock()
        mock_build_graph.return_value = mock_graph
        mock_graph_result = GraphAnalysisResult()
        mock_analyze_graph.return_value = mock_graph_result
        mock_apply_graph.return_value = mock_graph_result
        mock_dc = MagicMock()
        mock_dead_code.return_value = mock_dc
        mock_apply_dead.return_value = mock_dc
        mock_hw = MagicMock()
        mock_hardwiring.return_value = mock_hw
        mock_apply_hw.return_value = mock_hw
        mock_unsupported.return_value = {"go": 5}

        config = AigisCodeConfig(project_path=Path("/tmp/fake"))
        policy = AnalysisPolicy()
        store = MagicMock()

        result = run_deterministic_analysis(
            config=config,
            store=store,
            policy=policy,
            runtime_plugins=[],
        )

        assert isinstance(result, DeterministicResult)
        assert result.graph is mock_graph
        assert result.graph_result is mock_graph_result
        assert result.dead_code_result is mock_dc
        assert result.hardwiring_result is mock_hw
        assert result.unsupported_breakdown == {"go": 5}


# ---------------------------------------------------------------------------
# collect_external_analysis_for_report — signature + mock
# ---------------------------------------------------------------------------


class TestCollectExternalAnalysisForReport:
    """Test collect_external_analysis_for_report with mocks."""

    def test_signature(self):
        sig = inspect.signature(collect_external_analysis_for_report)
        params = set(sig.parameters.keys())
        assert "project_path" in params
        assert "output_dir" in params
        assert "run_id" in params
        assert "selected_tools" in params
        assert "existing_rules" in params
        assert "ctx" in params

    @patch("aigiscode.orchestration.filter_external_findings")
    @patch("aigiscode.orchestration.collect_external_analysis")
    def test_returns_tuple(self, mock_collect, mock_filter):
        from aigiscode.models import ExternalAnalysisResult, ExternalFinding

        finding = ExternalFinding(tool="ruff", message="test")
        raw = ExternalAnalysisResult(findings=[finding])
        mock_collect.return_value = raw
        mock_filter.return_value = (raw.findings, 0)

        result, excluded = collect_external_analysis_for_report(
            project_path=Path("/tmp/fake"),
            output_dir=Path("/tmp/fake/.aigiscode"),
            run_id="20260315_120000",
            selected_tools=["ruff"],
            existing_rules=[],
            ctx=None,
        )
        assert isinstance(result, ExternalAnalysisResult)
        assert excluded == 0


# ---------------------------------------------------------------------------
# build_report_data — signature + mock
# ---------------------------------------------------------------------------


class TestBuildReportData:
    """Test build_report_data with mocks."""

    def test_signature(self):
        sig = inspect.signature(build_report_data)
        params = set(sig.parameters.keys())
        assert "store" in params
        assert "project_path" in params
        assert "generated_at" in params
        assert "graph" in params
        assert "graph_result" in params
        assert "dead_code_result" in params
        assert "hardwiring_result" in params
        assert "review_result" in params
        assert "policy" in params

    @patch("aigiscode.orchestration.build_report_extensions")
    def test_returns_report_data(self, mock_extensions):
        from aigiscode.models import GraphAnalysisResult, ReportData
        from aigiscode.policy.models import AnalysisPolicy

        mock_extensions.return_value = {}
        store = MagicMock()
        store.get_file_count.return_value = 10
        store.get_symbol_count.return_value = 50
        store.get_dependency_count.return_value = 30
        store.get_language_breakdown.return_value = {"python": 8, "php": 2}

        report = build_report_data(
            store=store,
            project_path=Path("/tmp/fake"),
            generated_at=datetime(2026, 3, 15),
            graph=MagicMock(),
            graph_result=GraphAnalysisResult(),
            dead_code_result=MagicMock(total=5),
            hardwiring_result=MagicMock(total=3),
            review_result=None,
            security_review_result=None,
            external_analysis=None,
            runtime_plugins=[],
            policy=AnalysisPolicy(),
            unsupported_breakdown={"go": 2},
            synthesis_text="summary",
            envelopes_generated=7,
        )
        assert isinstance(report, ReportData)
        assert report.files_indexed == 10
        assert report.symbols_extracted == 50
        assert report.dependencies_found == 30
        assert report.synthesis == "summary"
        assert report.envelopes_generated == 7
        assert report.unsupported_source_files == 2
        assert report.unsupported_language_breakdown == {"go": 2}
        assert report.language_breakdown == {"python": 8, "php": 2}
