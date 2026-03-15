"""Shared analysis pipeline logic used by CLI commands.

This module provides the glue between the indexer, graph analyzers,
external security tools, extensions, and report builder.  Each public
function corresponds to a reusable pipeline step that ``cli.py``
commands call directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aigiscode.extensions import (
    ExternalPlugin,
    apply_dead_code_result_plugins,
    apply_graph_result_plugins,
    apply_hardwiring_result_plugins,
    build_report_extensions,
    load_external_plugins,
)
from aigiscode.graph.analyzer import analyze_graph
from aigiscode.graph.builder import build_file_graph
from aigiscode.graph.deadcode import analyze_dead_code
from aigiscode.graph.hardwiring import analyze_hardwiring
from aigiscode.indexer.parser import discover_unsupported_source_files
from aigiscode.models import (
    AigisCodeConfig,
    ExternalAnalysisResult,
    FeedbackLoop,
    GraphAnalysisResult,
    ReportData,
    ReviewResult,
)
from aigiscode.policy.models import AnalysisPolicy
from aigiscode.policy.plugins import resolve_policy
from aigiscode.rules.engine import filter_external_findings
from aigiscode.security.external import (
    SUPPORTED_SECURITY_TOOLS,
    collect_external_analysis,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RuntimeEnvironment:
    """Resolved policy and loaded runtime plugins."""

    policy: AnalysisPolicy
    runtime_plugins: list[ExternalPlugin] = field(default_factory=list)


@dataclass
class DeterministicResult:
    """Output of the deterministic (non-AI) analysis pipeline."""

    graph: Any  # nx.DiGraph
    graph_result: GraphAnalysisResult
    dead_code_result: Any  # DeadCodeResult
    hardwiring_result: Any  # HardwiringResult
    unsupported_breakdown: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def selected_external_tools(
    external_tools: list[str] | None,
    *,
    run_ruff_security: bool = False,
) -> list[str] | None:
    """Normalise the user-provided external tool selection.

    Returns ``None`` when no external tools should be run, or a concrete
    list of tool names (with ``"all"`` expanded).
    """
    tools: list[str] | None = None

    if external_tools:
        if "all" in external_tools:
            tools = list(SUPPORTED_SECURITY_TOOLS)
        else:
            tools = list(external_tools)

    if run_ruff_security:
        if tools is None:
            tools = ["ruff"]
        elif "ruff" not in tools:
            tools.append("ruff")

    return tools if tools else None


def combine_runtime_plugins(
    plugins_applied: list[str],
    external_plugins: list[ExternalPlugin],
) -> list[ExternalPlugin]:
    """Return only those external plugins whose ref appears in *plugins_applied*.

    The policy resolver prefixes external plugin refs with ``"module:"``
    when they contribute a policy patch.  This function filters the full
    loaded plugin list down to those that were actually applied.
    """
    applied_refs = {
        entry.removeprefix("module:")
        for entry in plugins_applied
        if entry.startswith("module:")
    }
    return [p for p in external_plugins if p.ref in applied_refs]


def resolve_runtime_environment(config: AigisCodeConfig) -> RuntimeEnvironment:
    """Load external plugins and resolve the full analysis policy.

    This is the first step every CLI command performs after parsing
    arguments.
    """
    external_plugins = load_external_plugins(config.plugin_modules or None)
    policy = resolve_policy(
        config.project_path,
        plugin_names=config.plugins or [],
        policy_file=config.policy_file,
        plugin_modules=config.plugin_modules or [],
        external_plugins=external_plugins,
    )
    runtime_plugins = combine_runtime_plugins(
        policy.plugins_applied,
        external_plugins,
    )
    return RuntimeEnvironment(policy=policy, runtime_plugins=runtime_plugins)


def run_deterministic_analysis(
    *,
    config: AigisCodeConfig,
    store: Any,
    policy: AnalysisPolicy,
    runtime_plugins: list[ExternalPlugin],
) -> DeterministicResult:
    """Execute the graph, dead-code, and hardwiring analysers.

    This is the deterministic (no-AI) pipeline that produces structural
    findings.  Plugin hooks are applied on each result before returning.
    """
    unsupported_breakdown = discover_unsupported_source_files(config)

    graph = build_file_graph(store, policy=policy.graph)
    graph_result = analyze_graph(graph, store, policy=policy.graph)
    graph_result = apply_graph_result_plugins(
        graph_result,
        runtime_plugins,
        graph=graph,
        store=store,
        project_path=config.project_path,
        policy=policy,
    )

    dead_code_result = analyze_dead_code(store, policy=policy.dead_code)
    dead_code_result = apply_dead_code_result_plugins(
        dead_code_result,
        runtime_plugins,
        store=store,
        project_path=config.project_path,
        policy=policy,
    )

    hardwiring_result = analyze_hardwiring(
        store,
        policy=policy.hardwiring,
        external_plugins=runtime_plugins,
        project_path=config.project_path,
    )
    hardwiring_result = apply_hardwiring_result_plugins(
        hardwiring_result,
        runtime_plugins,
        store=store,
        project_path=config.project_path,
        policy=policy,
    )

    return DeterministicResult(
        graph=graph,
        graph_result=graph_result,
        dead_code_result=dead_code_result,
        hardwiring_result=hardwiring_result,
        unsupported_breakdown=unsupported_breakdown,
    )


def collect_external_analysis_for_report(
    *,
    project_path: Path,
    output_dir: Path,
    run_id: str,
    selected_tools: list[str],
    existing_rules: list[Any],
    ctx: Any,
) -> tuple[ExternalAnalysisResult, int]:
    """Run external security tools and pre-filter their findings.

    Returns the (possibly filtered) :class:`ExternalAnalysisResult` and
    the number of findings excluded by saved rules.
    """
    result = collect_external_analysis(
        project_path=project_path,
        output_dir=output_dir,
        run_id=run_id,
        selected_tools=selected_tools,
    )

    excluded = 0
    if existing_rules and result.findings:
        filtered_findings, excluded = filter_external_findings(
            result.findings,
            existing_rules,
            ctx=ctx,
        )
        result = ExternalAnalysisResult(
            tool_runs=result.tool_runs,
            findings=filtered_findings,
        )

    return result, excluded


def build_report_data(
    *,
    store: Any,
    project_path: Path,
    generated_at: datetime,
    graph: Any,
    graph_result: GraphAnalysisResult,
    dead_code_result: Any,
    hardwiring_result: Any,
    review_result: ReviewResult | None,
    security_review_result: Any | None = None,
    external_analysis: ExternalAnalysisResult | None = None,
    runtime_plugins: list[ExternalPlugin],
    policy: AnalysisPolicy,
    unsupported_breakdown: dict[str, int],
    synthesis_text: str = "",
    envelopes_generated: int = 0,
) -> ReportData:
    """Assemble a :class:`ReportData` from all analysis artefacts."""
    extensions = build_report_extensions(
        runtime_plugins,
        report=None,
        graph=graph,
        store=store,
        project_path=project_path,
        policy=policy,
    )

    # Compute feedback loop metrics
    detected_total = (
        getattr(dead_code_result, "total", 0)
        + getattr(hardwiring_result, "total", 0)
    )
    rules_prefiltered = 0
    rules_generated = 0
    true_positives = 0
    false_positives = 0

    if review_result is not None:
        rules_prefiltered = getattr(review_result, "rules_prefiltered", 0)
        rules_generated = getattr(review_result, "rules_generated", 0)
        true_positives = review_result.true_positives
        false_positives = review_result.false_positives

    actionable_visible = true_positives
    accepted_by_policy = rules_prefiltered + false_positives

    feedback_loop = FeedbackLoop(
        detected_total=detected_total,
        actionable_visible=actionable_visible,
        accepted_by_policy=accepted_by_policy,
        rules_generated=rules_generated,
    )

    return ReportData(
        project_path=str(project_path),
        generated_at=generated_at,
        files_indexed=store.get_file_count(),
        symbols_extracted=store.get_symbol_count(),
        dependencies_found=store.get_dependency_count(),
        unsupported_source_files=sum(unsupported_breakdown.values()),
        unsupported_language_breakdown=unsupported_breakdown,
        graph_analysis=graph_result,
        dead_code=dead_code_result,
        hardwiring=hardwiring_result,
        review=review_result,
        envelopes_generated=envelopes_generated,
        synthesis=synthesis_text,
        language_breakdown=store.get_language_breakdown(),
        extensions=extensions,
        feedback_loop=feedback_loop,
    )
