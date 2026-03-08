"""CLI entry point for codexaudit.

Provides three commands: index, analyze, and report.
Uses Typer for CLI framework and Rich for beautiful output.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codexaudit.ai.backends import has_any_backend
from codexaudit import __version__
from codexaudit.models import CodexAuditConfig, ReportData

app = typer.Typer(
    name="codexaudit",
    help="AI-powered whole-codebase analysis tool.",
    no_args_is_help=True,
)
console = Console()


def _collect_detector_coverage(
    language_breakdown: dict[str, int],
) -> dict[str, list[str]]:
    from codexaudit.graph.deadcode import SUPPORTED_DEAD_CODE_LANGUAGES
    from codexaudit.graph.hardwiring import SUPPORTED_HARDWIRING_LANGUAGES

    indexed_languages = {
        language for language, count in language_breakdown.items() if count > 0
    }
    coverage: dict[str, list[str]] = {}

    dead_code_missing = sorted(indexed_languages - set(SUPPORTED_DEAD_CODE_LANGUAGES))
    if dead_code_missing:
        coverage["dead_code"] = dead_code_missing

    hardwiring_missing = sorted(indexed_languages - set(SUPPORTED_HARDWIRING_LANGUAGES))
    if hardwiring_missing:
        coverage["hardwiring"] = hardwiring_missing

    return coverage


def _format_detector_coverage(
    detector_coverage: dict[str, list[str]],
) -> str | None:
    if not detector_coverage:
        return None
    return "; ".join(
        f"{detector}: {', '.join(languages)}"
        for detector, languages in sorted(detector_coverage.items())
        if languages
    )


def _describe_project_type(
    *,
    project_path: Path,
    store,
    selected_plugins: list[str],
    is_laravel: bool,
) -> str:
    framework_labels: list[str] = []
    if is_laravel or "laravel" in selected_plugins:
        framework_labels.append("Laravel")

    language_rows = store.conn.execute(
        """
        SELECT language, COUNT(*) AS count
        FROM files
        GROUP BY language
        ORDER BY count DESC, language
        """
    ).fetchall()
    language_map = {
        "php": "PHP",
        "python": "Python",
        "typescript": "TypeScript",
        "javascript": "JavaScript",
        "vue": "Vue",
    }
    language_labels = [
        language_map.get(str(row["language"]).lower(), str(row["language"]).title())
        for row in language_rows
        if row["count"] > 0
    ]

    if framework_labels and language_labels:
        return (
            f"{'/'.join(framework_labels)} mixed-language project "
            f"({', '.join(language_labels[:4])})"
        )
    if framework_labels:
        return "/".join(framework_labels)
    if len(language_labels) > 1:
        return f"Mixed-language project ({', '.join(language_labels[:4])})"
    if language_labels:
        return f"{language_labels[0]} project"
    return f"Project at {project_path.name}"


def _combine_runtime_plugins(
    selected_plugins: list[str], external_plugins: list
) -> list:
    from codexaudit.builtin_runtime_plugins import load_builtin_runtime_plugins

    combined = [*load_builtin_runtime_plugins(selected_plugins), *external_plugins]
    deduped = []
    seen: set[str] = set()
    for plugin in combined:
        if plugin.ref in seen:
            continue
        seen.add(plugin.ref)
        deduped.append(plugin)
    return deduped


def _normalize_confidence_option(value: str | None, option_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in {"low", "medium", "high"}:
        console.print(
            f"[red]Error:[/red] {option_name} must be one of: low, medium, high"
        )
        raise typer.Exit(1)
    return normalized


def _resolve_project(project_path: str) -> Path:
    """Resolve and validate the project path."""
    path = Path(project_path).resolve()
    if not path.exists():
        console.print(f"[red]Error:[/red] Path does not exist: {path}")
        raise typer.Exit(1)
    if not path.is_dir():
        console.print(f"[red]Error:[/red] Path is not a directory: {path}")
        raise typer.Exit(1)
    return path


def _configure_logging(verbose: bool) -> None:
    """Configure logging level based on verbosity flag."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_header(title: str) -> None:
    console.print()
    console.print(Panel(f"[bold]{title}[/bold]", subtitle=f"codexaudit v{__version__}"))
    console.print()


def _merge_patch(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_patch(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            seen: set[str] = set()
            deduped: list = []
            for item in [*merged[key], *value]:
                marker = repr(item)
                if marker in seen:
                    continue
                seen.add(marker)
                deduped.append(item)
            merged[key] = deduped
        else:
            merged[key] = value
    return merged


def _collect_metrics(
    project_path: Path,
    db_path: Path,
    policy,
    output_dir: Path,
    external_plugins=None,
) -> dict[str, int]:
    from codexaudit.extensions import (
        apply_dead_code_result_plugins,
        apply_graph_result_plugins,
        apply_hardwiring_result_plugins,
    )
    from codexaudit.graph.analyzer import analyze_graph
    from codexaudit.graph.builder import build_file_graph
    from codexaudit.graph.deadcode import analyze_dead_code
    from codexaudit.graph.hardwiring import analyze_hardwiring
    from codexaudit.indexer.parser import discover_unsupported_source_files
    from codexaudit.indexer.store import IndexStore
    from codexaudit.rules.checks import StructuralContext
    from codexaudit.rules.engine import ensure_seed_rules, filter_findings, load_rules

    store = IndexStore(db_path)
    store.initialize()
    unsupported_breakdown = discover_unsupported_source_files(
        CodexAuditConfig(project_path=project_path)
    )

    graph = build_file_graph(store, policy=policy.graph)
    graph_result = analyze_graph(graph, store, policy=policy.graph)
    graph_result = apply_graph_result_plugins(
        graph_result,
        external_plugins or [],
        graph=graph,
        store=store,
        project_path=project_path,
        policy=policy,
    )
    dead_code = analyze_dead_code(store, policy=policy.dead_code)
    dead_code = apply_dead_code_result_plugins(
        dead_code,
        external_plugins or [],
        store=store,
        project_path=project_path,
        policy=policy,
    )
    hardwiring = analyze_hardwiring(
        store,
        policy=policy.hardwiring,
        external_plugins=external_plugins or [],
        project_path=project_path,
    )
    hardwiring = apply_hardwiring_result_plugins(
        hardwiring,
        external_plugins or [],
        store=store,
        project_path=project_path,
        policy=policy,
    )

    rules_path = output_dir / "rules.json"
    ensure_seed_rules(rules_path)
    rules = load_rules(rules_path)
    if rules:
        ctx = StructuralContext(store=store, project_root=project_path)
        dead_code, hardwiring, _excluded = filter_findings(
            dead_code,
            hardwiring,
            rules,
            ctx=ctx,
        )

    metrics = {
        "cycles": len(graph_result.strong_circular_dependencies)
        or len(graph_result.circular_dependencies),
        "violations": len(graph_result.layer_violations),
        "dead_code": dead_code.total,
        "hardwiring": hardwiring.total,
        "orphans": len(graph_result.orphan_files),
        "god_classes": len(graph_result.god_classes),
        "unsupported_source_files": sum(unsupported_breakdown.values()),
    }
    store.close()
    return metrics


def _score_metrics(metrics: dict[str, int]) -> int:
    return (
        metrics["cycles"] * 40
        + metrics["violations"] * 12
        + metrics["dead_code"] * 3
        + metrics["hardwiring"]
        + metrics["orphans"] * 2
        + metrics["god_classes"] * 5
    )


def _is_candidate_improvement(
    baseline: dict[str, int], candidate: dict[str, int]
) -> bool:
    metric_order = [
        "cycles",
        "violations",
        "dead_code",
        "hardwiring",
        "orphans",
        "god_classes",
    ]
    any_better = any(candidate[key] < baseline[key] for key in metric_order)
    any_worse = any(candidate[key] > baseline[key] for key in metric_order)
    if any_worse:
        return False
    if not any_better:
        return False
    return _score_metrics(candidate) <= _score_metrics(baseline)


@app.command()
def index(
    project_path: str = typer.Argument(..., help="Path to the project to index"),
    reset: bool = typer.Option(
        False, "--reset", "-r", help="Reset the index before indexing"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Enable debug logging"),
) -> None:
    """Build the codebase index (tree-sitter parsing + symbol extraction).

    Parses all supported source files, extracts symbols and dependencies,
    and stores everything in a SQLite database.
    """
    _configure_logging(verbose)
    path = _resolve_project(project_path)
    config = CodexAuditConfig(project_path=path)

    _print_header(f"Indexing: {path}")

    # Detect project type
    if config.is_laravel:
        console.print("[dim]Detected: Laravel project[/dim]")

    from codexaudit.indexer.store import IndexStore
    from codexaudit.indexer.parser import index_project

    store = IndexStore(config.db_path)
    if reset:
        console.print("[yellow]Resetting index...[/yellow]")
        store.reset()
    else:
        store.initialize()

    result = index_project(config, store)

    # Print summary
    console.print()
    table = Table(title="Index Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    table.add_row("Files indexed", str(result["files_indexed"]))
    if result.get("files_skipped", 0) > 0:
        table.add_row("Files skipped (unchanged)", str(result["files_skipped"]))
    if result.get("files_pruned", 0) > 0:
        table.add_row("Files pruned (stale)", str(result["files_pruned"]))
    table.add_row("Symbols extracted", str(result["symbols_extracted"]))
    table.add_row("Dependencies found", str(result["dependencies_found"]))
    console.print(table)

    # Language breakdown
    breakdown = store.get_language_breakdown()
    if breakdown:
        console.print()
        lang_table = Table(title="Languages")
        lang_table.add_column("Language", style="cyan")
        lang_table.add_column("Files", style="green", justify="right")
        for lang, count in sorted(breakdown.items(), key=lambda x: -x[1]):
            lang_table.add_row(lang, str(count))
        console.print(lang_table)

    if result["errors"]:
        console.print(
            f"\n[yellow]Warnings:[/yellow] {len(result['errors'])} files had issues"
        )
        for err in result["errors"][:5]:
            console.print(f"  [dim]{err}[/dim]")

    console.print(f"\n[green]Index stored at:[/green] {config.db_path}")
    store.close()


@app.command()
def analyze(
    project_path: str = typer.Argument(..., help="Path to the project to analyze"),
    skip_ai: bool = typer.Option(
        False, "--skip-ai", help="Skip AI workers (Codex/OpenAI)"
    ),
    skip_review: bool = typer.Option(
        False, "--skip-review", help="Skip AI finding review (Phase 2c)"
    ),
    skip_synthesis: bool = typer.Option(
        False, "--skip-synthesis", help="Skip AI synthesis"
    ),
    plugins: list[str] = typer.Option(
        None,
        "--plugin",
        "-P",
        help="Enable plugin profile (repeatable). Example: -P laravel -P newerp",
    ),
    policy_file: Path | None = typer.Option(
        None, "--policy-file", help="Optional JSON policy patch file"
    ),
    plugin_modules: list[str] = typer.Option(
        None,
        "--plugin-module",
        help="External Python plugin module path/name (repeatable)",
    ),
    dead_code_categories: list[str] = typer.Option(
        None,
        "--dead-code-category",
        help="Keep only selected dead-code categories (repeatable)",
    ),
    hardwiring_categories: list[str] = typer.Option(
        None,
        "--hardwiring-category",
        help="Keep only selected hardwiring categories (repeatable)",
    ),
    min_dead_code_confidence: str | None = typer.Option(
        None,
        "--min-dead-code-confidence",
        help="Filter dead-code findings below this confidence: low|medium|high",
    ),
    min_hardwiring_confidence: str | None = typer.Option(
        None,
        "--min-hardwiring-confidence",
        help="Filter hardwiring findings below this confidence: low|medium|high",
    ),
    analytical_mode: bool = typer.Option(
        False,
        "--analytical-mode",
        help="Ask Codex to propose project-specific policy tuning after analysis",
    ),
    max_workers: int = typer.Option(
        4, "--workers", "-w", help="Max parallel AI workers"
    ),
    reset: bool = typer.Option(
        False, "--reset", "-r", help="Reset index before analysis"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Enable debug logging"),
) -> None:
    """Run full analysis: index + graph analysis + AI workers + synthesis + report.

    This is the main command that runs all phases in sequence.
    """
    _configure_logging(verbose)
    path = _resolve_project(project_path)
    min_dead_code_confidence = _normalize_confidence_option(
        min_dead_code_confidence,
        "--min-dead-code-confidence",
    )
    min_hardwiring_confidence = _normalize_confidence_option(
        min_hardwiring_confidence,
        "--min-hardwiring-confidence",
    )
    config = CodexAuditConfig(
        project_path=path,
        max_workers=max_workers,
        skip_ai=skip_ai,
        skip_review=skip_review,
        skip_synthesis=skip_synthesis,
        plugins=plugins or [],
        policy_file=policy_file,
        analytical_mode=analytical_mode,
        plugin_modules=plugin_modules or [],
    )

    _print_header(f"Analyzing: {path}")

    if config.is_laravel:
        console.print("[dim]Detected: Laravel project[/dim]")

    from codexaudit.extensions import (
        apply_dead_code_result_plugins,
        apply_graph_result_plugins,
        apply_hardwiring_result_plugins,
        build_report_extensions,
        load_external_plugins,
    )
    from codexaudit.filters import filter_dead_code_result, filter_hardwiring_result
    from codexaudit.policy.plugins import resolve_policy

    external_plugins = load_external_plugins(config.plugin_modules)

    policy = resolve_policy(
        path,
        plugin_names=config.plugins,
        policy_file=config.policy_file,
        plugin_modules=config.plugin_modules,
        external_plugins=external_plugins,
    )
    runtime_plugins = _combine_runtime_plugins(
        policy.plugins_applied,
        external_plugins,
    )
    console.print(f"[dim]Policy plugins: {', '.join(policy.plugins_applied)}[/dim]")
    if runtime_plugins:
        console.print(
            f"[dim]Runtime plugins: {', '.join(plugin.name for plugin in runtime_plugins)}[/dim]"
        )

    # --- Phase 1: Index ---
    console.print("\n[bold blue]Phase 1: Indexing[/bold blue]")
    from codexaudit.indexer.store import IndexStore
    from codexaudit.indexer.parser import index_project

    store = IndexStore(config.db_path)
    if reset:
        store.reset()
    else:
        store.initialize()

    index_result = index_project(config, store)
    skipped_msg = ""
    if index_result.get("files_skipped", 0) > 0:
        skipped_msg = f" ({index_result['files_skipped']} unchanged, skipped)"
    pruned_msg = ""
    if index_result.get("files_pruned", 0) > 0:
        pruned_msg = f", {index_result['files_pruned']} stale pruned"
    console.print(
        f"  Indexed {index_result['files_indexed']} files{skipped_msg}, "
        f"{index_result['symbols_extracted']} symbols, "
        f"{index_result['dependencies_found']} dependencies{pruned_msg}"
    )
    if index_result.get("unsupported_source_files", 0):
        breakdown = ", ".join(
            f"{lang}={count}"
            for lang, count in index_result.get(
                "unsupported_language_breakdown", {}
            ).items()
        )
        console.print(
            "  [yellow]Coverage warning:[/yellow] "
            f"{index_result['unsupported_source_files']} unsupported source files skipped"
            + (f" ({breakdown})" if breakdown else "")
        )

    if index_result["files_indexed"] == 0 and index_result.get("files_skipped", 0) == 0:
        console.print(
            "[yellow]No files found to analyze. Check the project path and supported languages.[/yellow]"
        )
        store.close()
        raise typer.Exit(0)

    # --- Phase 2: Graph Analysis ---
    console.print("\n[bold blue]Phase 2: Graph Analysis[/bold blue]")
    from codexaudit.graph.builder import build_file_graph
    from codexaudit.graph.analyzer import analyze_graph

    graph = build_file_graph(store, policy=policy.graph)
    graph_result = analyze_graph(graph, store, policy=policy.graph)
    graph_result = apply_graph_result_plugins(
        graph_result,
        runtime_plugins,
        graph=graph,
        store=store,
        project_path=path,
        policy=policy,
    )

    console.print(
        f"  Graph: {graph_result.node_count} nodes, {graph_result.edge_count} edges"
    )
    strong_cycles = len(graph_result.strong_circular_dependencies)
    total_cycles = len(graph_result.circular_dependencies)
    if strong_cycles != total_cycles:
        console.print(
            f"  Circular dependencies: {strong_cycles} strong, {total_cycles} total"
        )
    else:
        console.print(f"  Circular dependencies: {total_cycles}")
    console.print(f"  God classes: {len(graph_result.god_classes)}")
    console.print(f"  Layer violations: {len(graph_result.layer_violations)}")
    console.print(f"  Bottleneck files: {len(graph_result.bottleneck_files)}")
    console.print(f"  Orphan files: {len(graph_result.orphan_files)}")
    console.print(
        f"  Runtime entry candidates: {len(graph_result.runtime_entry_candidates)}"
    )

    # --- Phase 2b: Dead Code & Hardwiring Analysis ---
    console.print("\n[bold blue]Phase 2b: Dead Code & Hardwiring Analysis[/bold blue]")
    from codexaudit.graph.deadcode import analyze_dead_code
    from codexaudit.graph.hardwiring import analyze_hardwiring

    dead_code_result = analyze_dead_code(store, policy=policy.dead_code)
    hardwiring_result = analyze_hardwiring(
        store,
        policy=policy.hardwiring,
        external_plugins=runtime_plugins,
        project_path=path,
    )
    dead_code_result = apply_dead_code_result_plugins(
        dead_code_result,
        runtime_plugins,
        store=store,
        project_path=path,
        policy=policy,
    )
    hardwiring_result = apply_hardwiring_result_plugins(
        hardwiring_result,
        runtime_plugins,
        store=store,
        project_path=path,
        policy=policy,
    )

    dc = dead_code_result
    console.print(
        f"  Dead code: {len(dc.unused_imports)} unused imports, "
        f"{len(dc.unused_methods)} unused private methods, "
        f"{len(dc.unused_properties)} unused properties, "
        f"{len(dc.abandoned_classes)} abandoned classes"
    )
    hw = hardwiring_result
    console.print(
        f"  Hardwiring: {hw.total} findings "
        f"({len(hw.magic_strings)} magic strings, "
        f"{len(hw.repeated_literals)} repeated literals, "
        f"{len(hw.hardcoded_entities)} hardcoded entities)"
    )

    # --- Phase 2c: Rule Filtering + AI Finding Review ---
    review_result = None
    rules_path = config.effective_output_dir / "rules.json"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    phase_suffix = " [yellow](AI review skipped)[/yellow]" if config.skip_review else ""
    console.print(
        f"\n[bold blue]Phase 2c: Rule Filtering + AI Finding Review[/bold blue]{phase_suffix}"
    )
    from codexaudit.rules.checks import StructuralContext
    from codexaudit.rules.engine import (
        append_rules,
        ensure_seed_rules,
        filter_findings,
        load_rules,
        save_rules,
    )

    seeded = ensure_seed_rules(rules_path)
    if seeded:
        console.print(f"  Created {seeded} seed exclusion rules")

    existing_rules = load_rules(rules_path)
    stale = [r for r in existing_rules if r.status == "stale"]
    disabled = [r for r in existing_rules if r.status == "disabled"]
    if stale:
        console.print(
            f"  [yellow]Warning:[/yellow] {len(stale)} stale rule(s): {', '.join(r.id for r in stale)}"
        )
    if disabled:
        console.print(f"  [dim]{len(disabled)} disabled rule(s) skipped[/dim]")

    ctx = StructuralContext(store=store, project_root=path)
    if existing_rules:
        dead_code_result, hardwiring_result, excluded = filter_findings(
            dead_code_result,
            hardwiring_result,
            existing_rules,
            ctx=ctx,
            run_id=run_id,
        )
        console.print(
            f"  Pre-filtered {excluded} findings using {len(existing_rules)} saved rules"
        )
        save_rules(rules_path, existing_rules)
    else:
        excluded = 0

    if dead_code_categories or min_dead_code_confidence:
        dead_code_result = filter_dead_code_result(
            dead_code_result,
            min_confidence=min_dead_code_confidence,
            categories=set(dead_code_categories or []),
        )
    if hardwiring_categories or min_hardwiring_confidence:
        hardwiring_result = filter_hardwiring_result(
            hardwiring_result,
            min_confidence=min_hardwiring_confidence,
            categories=set(hardwiring_categories or []),
        )

    remaining = (dead_code_result.total if dead_code_result else 0) + (
        hardwiring_result.total if hardwiring_result else 0
    )

    if config.skip_review:
        if excluded:
            from codexaudit.models import ReviewResult

            review_result = ReviewResult(rules_prefiltered=excluded)
    elif remaining > 0 and has_any_backend(
        allow_codex_cli_fallback=policy.ai.allow_codex_cli_fallback,
        allow_claude_fallback=policy.ai.allow_claude_fallback,
    ):
        console.print(f"  Reviewing {remaining} findings with Codex SDK + fallbacks...")

        from codexaudit.review.ai_reviewer import review_findings

        review_result, new_rules = asyncio.run(
            review_findings(
                dead_code_result,
                hardwiring_result,
                project_path=path,
                project_type=_describe_project_type(
                    project_path=path,
                    store=store,
                    selected_plugins=policy.plugins_applied,
                    is_laravel=config.is_laravel,
                ),
                store=store,
                review_model=policy.ai.review_model,
                allow_claude_fallback=policy.ai.allow_claude_fallback,
            )
        )
        review_result.rules_prefiltered = excluded

        if new_rules:
            added = append_rules(rules_path, new_rules)
            console.print(f"  Generated {added} new exclusion rules → {rules_path}")

        console.print(
            f"  Verdicts: {review_result.true_positives} true positives, "
            f"{review_result.false_positives} false positives, "
            f"{review_result.needs_context} needs context"
        )
    elif remaining == 0:
        console.print("  [green]All findings pre-filtered by existing rules[/green]")
    else:
        console.print(
            "  [yellow]Skipped:[/yellow] No Codex SDK key (or Claude fallback) available"
        )

    # --- Phase 3: AI Workers (Semantic Envelopes) ---
    envelopes_count = 0
    if not config.skip_ai:
        console.print(
            "\n[bold blue]Phase 3: Semantic Envelopes (AI Workers)[/bold blue]"
        )

        if has_any_backend(
            allow_codex_cli_fallback=policy.ai.allow_codex_cli_fallback,
            allow_claude_fallback=False,
        ):
            console.print(
                f"  Backend order: Codex SDK -> Codex CLI ({policy.ai.codex_model})"
            )

            from codexaudit.workers.codex import process_files

            envelopes_count = asyncio.run(
                process_files(
                    store,
                    config.project_path,
                    config.max_workers,
                    model=policy.ai.codex_model,
                )
            )
            console.print(f"  Generated {envelopes_count} semantic envelopes")
        else:
            console.print(
                "  [yellow]Skipped:[/yellow] No Codex SDK key (or CLI fallback) available"
            )
    else:
        console.print(
            "\n[bold blue]Phase 3: Semantic Envelopes[/bold blue] [yellow](skipped)[/yellow]"
        )

    # --- Phase 4: AI Synthesis ---
    synthesis_text = ""
    if not config.skip_synthesis:
        console.print("\n[bold blue]Phase 4: AI Synthesis[/bold blue]")

        if has_any_backend(
            allow_codex_cli_fallback=policy.ai.allow_codex_cli_fallback,
            allow_claude_fallback=policy.ai.allow_claude_fallback,
        ):
            from codexaudit.synthesis.claude import synthesize

            envelopes_by_layer = store.get_envelopes_by_layer()
            synthesis_text = asyncio.run(
                synthesize(
                    graph_result,
                    envelopes_by_layer,
                    model=policy.ai.synthesis_model,
                    allow_claude_fallback=policy.ai.allow_claude_fallback,
                )
            )
            if synthesis_text:
                console.print("  [green]Synthesis complete[/green]")
            else:
                console.print("  [yellow]Synthesis returned empty result[/yellow]")
        else:
            console.print(
                "  [yellow]Skipped:[/yellow] No Codex SDK key (or Claude fallback) available"
            )
    else:
        console.print(
            "\n[bold blue]Phase 4: AI Synthesis[/bold blue] [yellow](skipped)[/yellow]"
        )

    # --- Phase 5: Report Generation ---
    console.print("\n[bold blue]Phase 5: Report Generation[/bold blue]")

    # Use total DB counts (not just newly-indexed) for accurate reporting
    language_breakdown = store.get_language_breakdown()
    detector_coverage = _collect_detector_coverage(language_breakdown)

    report_data = ReportData(
        project_path=str(path),
        generated_at=datetime.now(),
        files_indexed=store.get_file_count(),
        symbols_extracted=store.get_symbol_count(),
        dependencies_found=store.get_dependency_count(),
        unsupported_source_files=index_result.get("unsupported_source_files", 0),
        unsupported_language_breakdown=index_result.get(
            "unsupported_language_breakdown", {}
        ),
        detector_coverage=detector_coverage,
        graph_analysis=graph_result,
        envelopes_generated=envelopes_count,
        synthesis=synthesis_text,
        language_breakdown=language_breakdown,
        dead_code=dead_code_result,
        hardwiring=hardwiring_result,
        review=review_result,
    )
    from codexaudit.report.contracts import build_contract_inventory
    from codexaudit.report.generator import write_reports

    report_data.extensions = {
        "contract_inventory": build_contract_inventory(store),
        **build_report_extensions(
            runtime_plugins,
            report=report_data,
            graph=graph,
            store=store,
            project_path=path,
            policy=policy,
        ),
    }

    md_path, json_path = write_reports(report_data, config.effective_output_dir)

    # Store metrics (run_id was set in Phase 2c)
    store.insert_metric(run_id, "files_indexed", index_result["files_indexed"])
    store.insert_metric(run_id, "symbols_extracted", index_result["symbols_extracted"])
    store.insert_metric(
        run_id,
        "circular_dependencies",
        len(graph_result.strong_circular_dependencies)
        or len(graph_result.circular_dependencies),
    )
    store.insert_metric(run_id, "god_classes", len(graph_result.god_classes))
    store.insert_metric(run_id, "layer_violations", len(graph_result.layer_violations))
    store.insert_metric(run_id, "dead_code_total", dead_code_result.total)
    store.insert_metric(run_id, "hardwiring_total", hardwiring_result.total)
    if review_result:
        store.insert_metric(
            run_id, "review_true_positives", review_result.true_positives
        )
        store.insert_metric(
            run_id, "review_false_positives", review_result.false_positives
        )

    store.close()

    console.print(f"  Markdown: {md_path}")
    console.print(f"  JSON: {json_path}")

    if config.analytical_mode:
        from codexaudit.policy.analytical import propose_policy_patch, save_policy_patch

        summary = {
            "files_indexed": report_data.files_indexed,
            "dependencies_found": report_data.dependencies_found,
            "cycles": len(graph_result.strong_circular_dependencies)
            or len(graph_result.circular_dependencies),
            "layer_violations": len(graph_result.layer_violations),
            "dead_code_total": dead_code_result.total,
            "hardwiring_total": hardwiring_result.total,
        }
        patch, backend = asyncio.run(propose_policy_patch(path, summary, policy))
        if patch:
            patch_path = save_policy_patch(config.effective_output_dir, patch)
            console.print(
                f"  Analytical mode: policy patch via {backend} → {patch_path}"
            )
        else:
            console.print("  Analytical mode: no AI policy patch generated")

    # Final summary panel
    console.print()
    _print_final_summary(report_data)


@app.command()
def report(
    project_path: str = typer.Argument(..., help="Path to the project"),
    plugins: list[str] = typer.Option(
        None,
        "--plugin",
        "-P",
        help="Enable plugin profile (repeatable). Example: -P laravel -P newerp",
    ),
    policy_file: Path | None = typer.Option(
        None, "--policy-file", help="Optional JSON policy patch file"
    ),
    plugin_modules: list[str] = typer.Option(
        None,
        "--plugin-module",
        help="External Python plugin module path/name (repeatable)",
    ),
    dead_code_categories: list[str] = typer.Option(
        None,
        "--dead-code-category",
        help="Keep only selected dead-code categories (repeatable)",
    ),
    hardwiring_categories: list[str] = typer.Option(
        None,
        "--hardwiring-category",
        help="Keep only selected hardwiring categories (repeatable)",
    ),
    min_dead_code_confidence: str | None = typer.Option(
        None,
        "--min-dead-code-confidence",
        help="Filter dead-code findings below this confidence: low|medium|high",
    ),
    min_hardwiring_confidence: str | None = typer.Option(
        None,
        "--min-hardwiring-confidence",
        help="Filter hardwiring findings below this confidence: low|medium|high",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Enable debug logging"),
) -> None:
    """Generate a report from existing index data.

    Run this after `codexaudit index` to generate reports without re-indexing.
    """
    _configure_logging(verbose)
    path = _resolve_project(project_path)
    min_dead_code_confidence = _normalize_confidence_option(
        min_dead_code_confidence,
        "--min-dead-code-confidence",
    )
    min_hardwiring_confidence = _normalize_confidence_option(
        min_hardwiring_confidence,
        "--min-hardwiring-confidence",
    )
    config = CodexAuditConfig(project_path=path)

    _print_header(f"Generating report for: {path}")

    if not config.db_path.exists():
        console.print(
            "[red]Error:[/red] No index found. Run `codexaudit index` or `codexaudit analyze` first."
        )
        raise typer.Exit(1)

    from codexaudit.extensions import (
        apply_dead_code_result_plugins,
        apply_graph_result_plugins,
        apply_hardwiring_result_plugins,
        build_report_extensions,
        load_external_plugins,
    )
    from codexaudit.filters import filter_dead_code_result, filter_hardwiring_result
    from codexaudit.indexer.parser import discover_unsupported_source_files
    from codexaudit.indexer.store import IndexStore
    from codexaudit.policy.plugins import resolve_policy
    from codexaudit.graph.builder import build_file_graph
    from codexaudit.graph.analyzer import analyze_graph
    from codexaudit.report.generator import write_reports

    external_plugins = load_external_plugins(plugin_modules or [])
    policy = resolve_policy(
        path,
        plugin_names=plugins or [],
        policy_file=policy_file,
        plugin_modules=plugin_modules or [],
        external_plugins=external_plugins,
    )
    runtime_plugins = _combine_runtime_plugins(
        policy.plugins_applied,
        external_plugins,
    )
    console.print(f"[dim]Policy plugins: {', '.join(policy.plugins_applied)}[/dim]")
    if runtime_plugins:
        console.print(
            f"[dim]Runtime plugins: {', '.join(plugin.name for plugin in runtime_plugins)}[/dim]"
        )

    store = IndexStore(config.db_path)
    store.initialize()
    unsupported_breakdown = discover_unsupported_source_files(config)

    # Run graph analysis on existing data
    graph = build_file_graph(store, policy=policy.graph)
    graph_result = analyze_graph(graph, store, policy=policy.graph)
    graph_result = apply_graph_result_plugins(
        graph_result,
        runtime_plugins,
        graph=graph,
        store=store,
        project_path=path,
        policy=policy,
    )

    # Run dead code & hardwiring analysis
    from codexaudit.graph.deadcode import analyze_dead_code
    from codexaudit.graph.hardwiring import analyze_hardwiring

    dead_code_result = analyze_dead_code(store, policy=policy.dead_code)
    hardwiring_result = analyze_hardwiring(
        store,
        policy=policy.hardwiring,
        external_plugins=runtime_plugins,
        project_path=path,
    )
    dead_code_result = apply_dead_code_result_plugins(
        dead_code_result,
        runtime_plugins,
        store=store,
        project_path=path,
        policy=policy,
    )
    hardwiring_result = apply_hardwiring_result_plugins(
        hardwiring_result,
        runtime_plugins,
        store=store,
        project_path=path,
        policy=policy,
    )

    # Apply saved exclusion rules (pre-filter only, no AI review in report mode)
    from codexaudit.rules.checks import StructuralContext
    from codexaudit.rules.engine import load_rules, filter_findings, ensure_seed_rules

    rules_path = config.effective_output_dir / "rules.json"
    ensure_seed_rules(rules_path)
    existing_rules = load_rules(rules_path)
    review_result = None

    ctx = StructuralContext(store=store, project_root=path)

    if existing_rules:
        dead_code_result, hardwiring_result, excluded = filter_findings(
            dead_code_result,
            hardwiring_result,
            existing_rules,
            ctx=ctx,
        )
        if excluded:
            console.print(
                f"  Pre-filtered {excluded} findings using {len(existing_rules)} saved rules"
            )
            from codexaudit.models import ReviewResult

            review_result = ReviewResult(rules_prefiltered=excluded)

    if dead_code_categories or min_dead_code_confidence:
        dead_code_result = filter_dead_code_result(
            dead_code_result,
            min_confidence=min_dead_code_confidence,
            categories=set(dead_code_categories or []),
        )
    if hardwiring_categories or min_hardwiring_confidence:
        hardwiring_result = filter_hardwiring_result(
            hardwiring_result,
            min_confidence=min_hardwiring_confidence,
            categories=set(hardwiring_categories or []),
        )

    language_breakdown = store.get_language_breakdown()
    detector_coverage = _collect_detector_coverage(language_breakdown)

    report_data = ReportData(
        project_path=str(path),
        generated_at=datetime.now(),
        files_indexed=store.get_file_count(),
        symbols_extracted=store.get_symbol_count(),
        dependencies_found=store.get_dependency_count(),
        unsupported_source_files=sum(unsupported_breakdown.values()),
        unsupported_language_breakdown=unsupported_breakdown,
        detector_coverage=detector_coverage,
        graph_analysis=graph_result,
        envelopes_generated=store.get_envelope_count(),
        synthesis="",
        language_breakdown=language_breakdown,
        dead_code=dead_code_result,
        hardwiring=hardwiring_result,
        review=review_result,
    )
    from codexaudit.report.contracts import build_contract_inventory

    report_data.extensions = {
        "contract_inventory": build_contract_inventory(store),
        **build_report_extensions(
            runtime_plugins,
            report=report_data,
            graph=graph,
            store=store,
            project_path=path,
            policy=policy,
        ),
    }

    md_path, json_path = write_reports(report_data, config.effective_output_dir)
    store.close()

    console.print(f"  Markdown: {md_path}")
    console.print(f"  JSON: {json_path}")

    console.print()
    _print_final_summary(report_data)


@app.command()
def tune(
    project_path: str = typer.Argument(..., help="Path to the project"),
    plugins: list[str] = typer.Option(
        None,
        "--plugin",
        "-P",
        help="Enable plugin profile (repeatable). Example: -P laravel -P newerp",
    ),
    policy_file: Path | None = typer.Option(
        None, "--policy-file", help="Optional JSON policy patch file"
    ),
    plugin_modules: list[str] = typer.Option(
        None,
        "--plugin-module",
        help="External Python plugin module path/name (repeatable)",
    ),
    iterations: int = typer.Option(
        1,
        "--iterations",
        "-i",
        min=1,
        max=5,
        help="Number of trial-and-error tuning rounds",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-V", help="Enable debug logging"),
) -> None:
    """Run AI-assisted trial-and-error policy tuning against existing index."""
    _configure_logging(verbose)
    path = _resolve_project(project_path)
    config = CodexAuditConfig(project_path=path)

    _print_header(f"Tuning policy for: {path}")

    if not config.db_path.exists():
        console.print(
            "[red]Error:[/red] No index found. Run `codexaudit index` or `codexaudit analyze` first."
        )
        raise typer.Exit(1)

    from codexaudit.extensions import load_external_plugins
    from codexaudit.policy.analytical import propose_policy_patch
    from codexaudit.policy.plugins import resolve_policy

    external_plugins = load_external_plugins(plugin_modules or [])

    patch_state: dict = {}
    patch_state_path = config.effective_output_dir / "policy.tuning-state.json"

    for turn in range(1, iterations + 1):
        if patch_state:
            patch_state_path.parent.mkdir(parents=True, exist_ok=True)
            patch_state_path.write_text(
                json.dumps(patch_state, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        active_policy_file = patch_state_path if patch_state else policy_file

        policy = resolve_policy(
            path,
            plugin_names=plugins or [],
            policy_file=active_policy_file,
            plugin_modules=plugin_modules or [],
            external_plugins=external_plugins,
        )
        runtime_plugins = _combine_runtime_plugins(
            policy.plugins_applied,
            external_plugins,
        )

        baseline = _collect_metrics(
            path,
            config.db_path,
            policy,
            config.effective_output_dir,
            external_plugins=runtime_plugins,
        )
        baseline_score = _score_metrics(baseline)

        summary = {
            "plugins_applied": policy.plugins_applied,
            **baseline,
            "score": baseline_score,
        }

        patch, backend = asyncio.run(propose_policy_patch(path, summary, policy))
        if not patch:
            console.print(f"  Round {turn}: no patch generated (backend={backend})")
            break

        candidate_patch = _merge_patch(patch_state, patch)
        candidate_path = config.effective_output_dir / "policy.candidate.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(candidate_patch, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        candidate_policy = resolve_policy(
            path,
            plugin_names=plugins or [],
            policy_file=candidate_path,
            plugin_modules=plugin_modules or [],
            external_plugins=external_plugins,
        )
        candidate_runtime_plugins = _combine_runtime_plugins(
            candidate_policy.plugins_applied,
            external_plugins,
        )
        candidate = _collect_metrics(
            path,
            config.db_path,
            candidate_policy,
            config.effective_output_dir,
            external_plugins=candidate_runtime_plugins,
        )
        candidate_score = _score_metrics(candidate)

        table = Table(title=f"Tuning Round {turn}")
        table.add_column("Metric", style="cyan")
        table.add_column("Baseline", style="yellow", justify="right")
        table.add_column("Candidate", style="green", justify="right")
        table.add_row("Score", str(baseline_score), str(candidate_score))
        for key in [
            "cycles",
            "violations",
            "dead_code",
            "hardwiring",
            "orphans",
            "god_classes",
        ]:
            table.add_row(key, str(baseline[key]), str(candidate[key]))
        console.print(table)

        if _is_candidate_improvement(baseline, candidate):
            patch_state = candidate_patch
            console.print(f"  [green]Accepted[/green] round {turn} patch via {backend}")
        elif any(
            candidate[key] > baseline[key]
            for key in [
                "cycles",
                "violations",
                "dead_code",
                "hardwiring",
                "orphans",
                "god_classes",
            ]
        ):
            console.print(
                f"  [yellow]Rejected[/yellow] round {turn} patch (regression detected in one or more metrics)"
            )
            break
        elif candidate_score == baseline_score:
            console.print(
                f"  [yellow]Rejected[/yellow] round {turn} patch (no measurable improvement)"
            )
            break
        else:
            console.print(
                f"  [yellow]Rejected[/yellow] round {turn} patch (score worsened)"
            )
            break

    optimized_path = config.effective_output_dir / "policy.optimized.json"
    optimized_path.parent.mkdir(parents=True, exist_ok=True)
    optimized_path.write_text(
        json.dumps(patch_state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    console.print(f"\n[green]Optimized patch saved:[/green] {optimized_path}")


@app.command()
def info(
    project_path: str = typer.Argument(..., help="Path to the project"),
) -> None:
    """Show information about an existing index."""
    path = _resolve_project(project_path)
    config = CodexAuditConfig(project_path=path)

    if not config.db_path.exists():
        console.print("[red]No index found.[/red] Run `codexaudit index` first.")
        raise typer.Exit(1)

    from codexaudit.indexer.store import IndexStore

    store = IndexStore(config.db_path)
    store.initialize()

    _print_header(f"Index Info: {path}")

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")
    table.add_row("Files", str(store.get_file_count()))
    table.add_row("Symbols", str(store.get_symbol_count()))
    table.add_row("Dependencies", str(store.get_dependency_count()))
    table.add_row("Envelopes", str(store.get_envelope_count()))
    console.print(table)

    breakdown = store.get_language_breakdown()
    if breakdown:
        console.print()
        lang_table = Table(title="Languages")
        lang_table.add_column("Language", style="cyan")
        lang_table.add_column("Files", style="green", justify="right")
        for lang, count in sorted(breakdown.items(), key=lambda x: -x[1]):
            lang_table.add_row(lang, str(count))
        console.print(lang_table)

    console.print(f"\n[dim]Database: {config.db_path}[/dim]")
    store.close()


@app.command("plugins")
def plugins_command() -> None:
    """List built-in plugin profiles."""
    from codexaudit.policy.plugins import list_plugins

    _print_header("Available Plugins")
    table = Table()
    table.add_column("Plugin", style="cyan")
    table.add_column("Description", style="green")
    for name, description in sorted(list_plugins().items()):
        table.add_row(name, description)
    console.print(table)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
) -> None:
    """codexaudit - AI-powered whole-codebase analysis tool."""
    if version:
        console.print(f"codexaudit v{__version__}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(0)


def _print_final_summary(report: ReportData) -> None:
    """Print a final summary panel."""
    ga = report.graph_analysis

    # Determine health color
    issues = (
        (len(ga.strong_circular_dependencies) or len(ga.circular_dependencies)) * 3
        + len(ga.god_classes) * 2
        + len(ga.layer_violations)
    )

    if issues == 0:
        health = "[green]HEALTHY[/green]"
    elif issues < 10:
        health = "[yellow]NEEDS ATTENTION[/yellow]"
    else:
        health = "[red]CRITICAL[/red]"
    has_partial_coverage = report.unsupported_source_files or report.detector_coverage
    if has_partial_coverage:
        if issues == 0:
            health = "[yellow]PARTIAL COVERAGE[/yellow]"
        elif issues < 10:
            health = "[yellow]NEEDS ATTENTION (PARTIAL COVERAGE)[/yellow]"
        else:
            health = "[red]CRITICAL (PARTIAL COVERAGE)[/red]"

    summary_lines = [
        f"Health: {health}",
        f"Files: {report.files_indexed} | Symbols: {report.symbols_extracted} | Deps: {report.dependencies_found}",
        f"Cycles: {len(ga.strong_circular_dependencies) or len(ga.circular_dependencies)} | God Classes: {len(ga.god_classes)} | "
        f"Violations: {len(ga.layer_violations)} | Orphans: {len(ga.orphan_files)}",
    ]
    if report.unsupported_source_files:
        breakdown = ", ".join(
            f"{lang}={count}"
            for lang, count in sorted(
                report.unsupported_language_breakdown.items(), key=lambda x: -x[1]
            )
        )
        summary_lines.append(
            f"Coverage Warning: {report.unsupported_source_files} unsupported source files skipped"
            + (f" ({breakdown})" if breakdown else "")
        )
    detector_coverage_text = _format_detector_coverage(report.detector_coverage)
    if detector_coverage_text:
        summary_lines.append(f"Detector Coverage: partial ({detector_coverage_text})")

    if report.dead_code:
        summary_lines.append(f"Dead Code: {report.dead_code.total} findings")
    if report.hardwiring:
        summary_lines.append(f"Hardwiring: {report.hardwiring.total} findings")
    if report.review:
        r = report.review
        parts = []
        if r.rules_prefiltered:
            parts.append(f"{r.rules_prefiltered} pre-filtered")
        if r.true_positives:
            parts.append(f"{r.true_positives} confirmed")
        if r.false_positives:
            parts.append(f"{r.false_positives} false positives")
        if parts:
            summary_lines.append(f"AI Review: {', '.join(parts)}")

    console.print(Panel("\n".join(summary_lines), title="Analysis Complete"))
