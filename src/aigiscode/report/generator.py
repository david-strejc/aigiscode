"""Report generation module.

Generates a Markdown report and JSON data file from analysis results.
"""

from __future__ import annotations

import json
from pathlib import Path

from aigiscode.models import ReportData


def generate_markdown_report(report: ReportData) -> str:
    """Generate a comprehensive Markdown report from the analysis data."""
    lines: list[str] = []

    lines.append("# AigisCode Report")
    lines.append("")
    lines.append(f"**Project**: `{report.project_path}`")
    lines.append(f"**Generated**: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("**aigiscode v0.1.0**")
    lines.append("")

    # --- Language breakdown ---
    lines.append("## Overview")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Files indexed | {report.files_indexed} |")
    lines.append(f"| Symbols extracted | {report.symbols_extracted} |")
    lines.append(f"| Dependencies found | {report.dependencies_found} |")
    lines.append(f"| Semantic envelopes | {report.envelopes_generated} |")
    if report.unsupported_source_files:
        lines.append(
            f"| Unsupported source files skipped | {report.unsupported_source_files} |"
        )
    if report.detector_coverage:
        lines.append(
            f"| Detector partial coverage | {_format_detector_coverage(report.detector_coverage)} |"
        )
    lines.append("")

    if report.language_breakdown:
        lines.append("### Language Breakdown")
        lines.append("")
        lines.append("| Language | Files |")
        lines.append("|----------|-------|")
        for lang, count in sorted(
            report.language_breakdown.items(), key=lambda x: -x[1]
        ):
            lines.append(f"| {lang} | {count} |")
        lines.append("")

    if report.unsupported_language_breakdown:
        lines.append("### Unsupported Source Languages")
        lines.append("")
        lines.append("| Language | Files |")
        lines.append("|----------|-------|")
        for lang, count in sorted(
            report.unsupported_language_breakdown.items(), key=lambda x: -x[1]
        ):
            lines.append(f"| {lang} | {count} |")
        lines.append("")

    if report.detector_coverage:
        lines.append("### Detector Coverage Warnings")
        lines.append("")
        lines.append("| Detector | Missing indexed languages |")
        lines.append("|----------|---------------------------|")
        for detector, languages in sorted(report.detector_coverage.items()):
            lines.append(f"| {detector} | {', '.join(languages)} |")
        lines.append("")

    ga = report.graph_analysis

    # --- Executive Summary (from synthesis or auto-generated) ---
    lines.append("## Executive Summary")
    lines.append("")
    if report.synthesis:
        lines.append(report.synthesis)
    else:
        lines.append(_auto_summary(report))
    lines.append("")

    # --- Architecture Health ---
    lines.append("## Architecture Health")
    lines.append("")

    lines.append(
        f"**Graph**: {ga.node_count} nodes, {ga.edge_count} edges, density={ga.density}"
    )
    lines.append("")

    # Circular dependencies
    strong_cycle_count = len(ga.strong_circular_dependencies)
    total_cycle_count = len(ga.circular_dependencies)
    lines.append(f"### Strong Circular Dependencies ({strong_cycle_count})")
    lines.append("")
    if strong_cycle_count != total_cycle_count:
        lines.append(
            f"Total cycles including load/bootstrap edges: {total_cycle_count}"
        )
        lines.append("")
    if ga.strong_circular_dependencies:
        for i, cycle in enumerate(ga.strong_circular_dependencies[:20], 1):
            cycle_str = " -> ".join(cycle) + " -> " + cycle[0]
            lines.append(f"{i}. `{cycle_str}`")
        if strong_cycle_count > 20:
            lines.append(f"\n*... and {strong_cycle_count - 20} more cycles*")
    else:
        lines.append("No strong circular dependencies detected.")
    lines.append("")

    # Layer violations
    lines.append(f"### Layer Violations ({len(ga.layer_violations)})")
    lines.append("")
    if ga.layer_violations:
        lines.append("| Source | Source Layer | Target | Target Layer | Violation |")
        lines.append("|--------|-------------|--------|-------------|-----------|")
        for v in ga.layer_violations[:30]:
            lines.append(
                f"| `{v.source_file}` | {v.source_layer.value} | `{v.target_name}` | {v.target_layer.value} | {v.violation} |"
            )
        if len(ga.layer_violations) > 30:
            lines.append(f"\n*... and {len(ga.layer_violations) - 30} more violations*")
    else:
        lines.append("No layer violations detected.")
    lines.append("")

    # Coupling
    lines.append("### Module Coupling (top 15 most unstable)")
    lines.append("")
    if ga.coupling_metrics:
        lines.append("| Module | Afferent (Ca) | Efferent (Ce) | Instability (I) |")
        lines.append("|--------|---------------|---------------|-----------------|")
        for m in ga.coupling_metrics[:15]:
            lines.append(
                f"| `{m.module}` | {m.afferent} | {m.efferent} | {m.instability} |"
            )
    else:
        lines.append("No coupling data available.")
    lines.append("")

    # --- Code Quality ---
    lines.append("## Code Quality")
    lines.append("")

    # God classes
    lines.append(f"### God Classes ({len(ga.god_classes)})")
    lines.append("")
    if ga.god_classes:
        lines.append("| Class | File | Methods | Dependencies | Lines |")
        lines.append("|-------|------|---------|-------------|-------|")
        for g in ga.god_classes[:20]:
            lines.append(
                f"| `{g.name}` | `{g.file_path}` | {g.method_count} | {g.dependency_count} | {g.line_count} |"
            )
    else:
        lines.append(
            "No god classes detected (threshold: 15+ methods or 10+ dependencies)."
        )
    lines.append("")

    # Bottlenecks
    lines.append("### Bottleneck Files (top 10)")
    lines.append("")
    if ga.bottleneck_files:
        lines.append(
            "Files with highest betweenness centrality (changes have the widest blast radius):"
        )
        lines.append("")
        lines.append("| File | Centrality Score |")
        lines.append("|------|-----------------|")
        for path, score in ga.bottleneck_files[:10]:
            lines.append(f"| `{path}` | {score} |")
    else:
        lines.append("No bottlenecks detected.")
    lines.append("")

    # Orphan files
    lines.append(f"### Likely Orphan Files ({len(ga.orphan_files)} files)")
    lines.append("")
    if ga.orphan_files:
        lines.append(
            "Files with outgoing dependencies but zero incoming dependencies, excluding known runtime entry candidates:"
        )
        lines.append("")
        for f in ga.orphan_files[:30]:
            lines.append(f"- `{f}`")
        if len(ga.orphan_files) > 30:
            lines.append(f"\n*... and {len(ga.orphan_files) - 30} more*")
    else:
        lines.append("No orphan files detected.")
    lines.append("")

    lines.append(
        f"### Runtime Entry Candidates ({len(ga.runtime_entry_candidates)} files)"
    )
    lines.append("")
    if ga.runtime_entry_candidates:
        lines.append(
            "Files with zero incoming dependencies that appear to be loader-driven or configured entrypoints:"
        )
        lines.append("")
        for f in ga.runtime_entry_candidates[:30]:
            lines.append(f"- `{f}`")
        if len(ga.runtime_entry_candidates) > 30:
            lines.append(f"\n*... and {len(ga.runtime_entry_candidates) - 30} more*")
    else:
        lines.append("No runtime entry candidates detected.")
    lines.append("")

    # --- Dead Code Analysis ---
    if report.dead_code:
        dc = report.dead_code
        lines.append(f"## Dead Code Analysis ({dc.total} findings)")
        lines.append("")

        sections = [
            ("Unused Imports", dc.unused_imports),
            ("Unused Methods", dc.unused_methods),
            ("Unused Properties", dc.unused_properties),
            ("Abandoned Classes", dc.abandoned_classes),
        ]

        for section_name, findings in sections:
            if not findings:
                continue
            lines.append(f"### {section_name} ({len(findings)})")
            lines.append("")
            lines.append("| File | Symbol | Confidence | Detail |")
            lines.append("|------|--------|------------|--------|")
            for f in findings[:30]:
                detail = f.detail[:50] + "..." if len(f.detail) > 50 else f.detail
                lines.append(
                    f"| `{f.file_path}:{f.line}` | `{f.name}` | {f.confidence} | {detail} |"
                )
            if len(findings) > 30:
                lines.append(f"\n*... and {len(findings) - 30} more*")
            lines.append("")

        if dc.total == 0:
            lines.append("No dead code detected.")
            lines.append("")

    # --- Hardwiring Analysis ---
    if report.hardwiring:
        hw = report.hardwiring
        lines.append(f"## Hardwiring Analysis ({hw.total} findings)")
        lines.append("")

        sections = [
            ("Magic Strings", hw.magic_strings),
            ("Repeated Literals", hw.repeated_literals),
            ("Hardcoded Entities", hw.hardcoded_entities),
            ("Hardcoded Network", hw.hardcoded_network),
            ("env() Outside Config", hw.env_outside_config),
        ]

        for section_name, findings in sections:
            if not findings:
                continue
            lines.append(f"### {section_name} ({len(findings)})")
            lines.append("")
            lines.append("| File | Value | Severity | Confidence | Suggestion |")
            lines.append("|------|-------|----------|------------|------------|")
            for f in findings[:30]:
                val = f.value[:40] + "..." if len(f.value) > 40 else f.value
                sug = (
                    f.suggestion[:50] + "..."
                    if len(f.suggestion) > 50
                    else f.suggestion
                )
                lines.append(
                    f"| `{f.file_path}:{f.line}` | `{val}` | {f.severity} | {f.confidence} | {sug} |"
                )
            if len(findings) > 30:
                lines.append(f"\n*... and {len(findings) - 30} more*")
            lines.append("")

        if hw.total == 0:
            lines.append("No hardwiring issues detected.")
            lines.append("")

    security_summary = _generate_security_summary(report)
    if security_summary["total_findings"] > 0:
        lines.append("## Security Analysis")
        lines.append("")
        lines.append("| Signal | Count |")
        lines.append("|--------|-------|")
        lines.append(
            f"| Hardcoded network endpoints | {security_summary['hardcoded_network']} |"
        )
        lines.append(
            f"| Environment reads outside config | {security_summary['env_outside_config']} |"
        )
        lines.append(
            f"| High-severity security signals | {security_summary['high_severity']} |"
        )
        if security_summary["ai_confirmed"]:
            lines.append(
                f"| AI-confirmed security findings | {security_summary['ai_confirmed']} |"
            )
        lines.append("")

        top_findings = security_summary["top_findings"]
        if top_findings:
            lines.append("### Highest-Signal Findings")
            lines.append("")
            lines.append("| File | Category | Value | Severity | Confidence |")
            lines.append("|------|----------|-------|----------|------------|")
            for finding in top_findings:
                lines.append(
                    f"| `{finding['file']}:{finding['line']}` | {finding['category']} | "
                    f"`{finding['value']}` | {finding['severity']} | {finding['confidence']} |"
                )
            lines.append("")

    # --- AI Finding Review ---
    if report.review:
        rv = report.review
        lines.append("## AI Finding Review")
        lines.append("")

        # Summary table
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        if rv.rules_prefiltered:
            lines.append(f"| Pre-filtered by rules | {rv.rules_prefiltered} |")
        if rv.total_reviewed:
            lines.append(f"| Reviewed by AI | {rv.total_reviewed} |")
            lines.append(f"| True positives | {rv.true_positives} |")
            lines.append(f"| False positives | {rv.false_positives} |")
            lines.append(f"| Needs context | {rv.needs_context} |")
        if rv.rules_generated:
            lines.append(f"| New rules generated | {rv.rules_generated} |")
        lines.append("")

        # Confirmed issues
        confirmed = [v for v in rv.verdicts if v.verdict == "true_positive"]
        if confirmed:
            lines.append(f"### Confirmed Issues ({len(confirmed)})")
            lines.append("")
            lines.append("| File | Category | Name/Value | Reason |")
            lines.append("|------|----------|------------|--------|")
            for v in confirmed[:30]:
                name = v.name or v.value
                reason = v.reason[:60] + "..." if len(v.reason) > 60 else v.reason
                lines.append(
                    f"| `{v.file_path}:{v.line}` | {v.category} | `{name}` | {reason} |"
                )
            if len(confirmed) > 30:
                lines.append(f"\n*... and {len(confirmed) - 30} more*")
            lines.append("")

        # Needs manual review
        manual = [v for v in rv.verdicts if v.verdict == "needs_context"]
        if manual:
            lines.append(f"### Needs Manual Review ({len(manual)})")
            lines.append("")
            lines.append("| File | Category | Name/Value | Reason |")
            lines.append("|------|----------|------------|--------|")
            for v in manual[:30]:
                name = v.name or v.value
                reason = v.reason[:60] + "..." if len(v.reason) > 60 else v.reason
                lines.append(
                    f"| `{v.file_path}:{v.line}` | {v.category} | `{name}` | {reason} |"
                )
            if len(manual) > 30:
                lines.append(f"\n*... and {len(manual) - 30} more*")
            lines.append("")

    if report.extensions:
        lines.append("## Extensions")
        lines.append("")
        for name, payload in sorted(report.extensions.items()):
            lines.append(f"### {name}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(payload, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")

    # --- Recommendations ---
    lines.append("## Recommendations")
    lines.append("")
    recs = _generate_recommendations(report)
    for i, rec in enumerate(recs, 1):
        lines.append(f"{i}. **{rec['title']}**: {rec['description']}")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append("*Generated by aigiscode v0.1.0*")

    return "\n".join(lines)


def generate_json_report(report: ReportData) -> dict:
    """Generate a structured JSON report from the analysis data."""
    ga = report.graph_analysis
    graph_analysis = _serialize_graph_analysis(ga)

    return {
        "version": "0.1.0",
        "project_path": report.project_path,
        "generated_at": report.generated_at.isoformat(),
        "summary": {
            "files_indexed": report.files_indexed,
            "symbols_extracted": report.symbols_extracted,
            "dependencies_found": report.dependencies_found,
            "envelopes_generated": report.envelopes_generated,
            "language_breakdown": report.language_breakdown,
            "unsupported_source_files": report.unsupported_source_files,
            "unsupported_language_breakdown": report.unsupported_language_breakdown,
            "detector_coverage": report.detector_coverage,
        },
        "graph_analysis": graph_analysis,
        "graph": {
            "nodes": graph_analysis["node_count"],
            "edges": graph_analysis["edge_count"],
            "density": graph_analysis["density"],
        },
        "circular_dependencies": graph_analysis["circular_dependencies"],
        "strong_circular_dependencies": graph_analysis["strong_circular_dependencies"],
        "coupling_metrics": graph_analysis["coupling_metrics"],
        "god_classes": graph_analysis["god_classes"],
        "bottlenecks": graph_analysis["bottleneck_files"],
        "layer_violations": graph_analysis["layer_violations"],
        "orphan_files": graph_analysis["orphan_files"],
        "runtime_entry_candidates": graph_analysis["runtime_entry_candidates"],
        "dead_code": _serialize_dead_code(report.dead_code)
        if report.dead_code
        else None,
        "hardwiring": _serialize_hardwiring(report.hardwiring)
        if report.hardwiring
        else None,
        "security": _generate_security_summary(report),
        "review": _serialize_review(report.review) if report.review else None,
        "extensions": report.extensions,
        "recommendations": _generate_recommendations(report),
    }


def allocate_archive_stem(output_dir: Path, timestamp: str) -> str:
    """Return a unique archive stem, appending _N if the directory already exists."""
    archive_dir = output_dir / "reports"
    candidate = timestamp
    counter = 0
    while (archive_dir / candidate).exists():
        counter += 1
        candidate = f"{timestamp}_{counter}"
    return candidate


def write_reports(
    report: ReportData, output_dir: Path, archive_stem: str | None = None
) -> tuple[Path, Path]:
    """Write both Markdown and JSON reports to the output directory.

    Returns (markdown_path, json_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "aigiscode-report.md"
    json_path = output_dir / "aigiscode-report.json"
    archive_dir = output_dir / "reports"

    if archive_stem:
        stem_dir = archive_dir / archive_stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        archive_md_path = stem_dir / "aigiscode-report.md"
        archive_json_path = stem_dir / "aigiscode-report.json"
    else:
        timestamp = report.generated_at.strftime("%Y%m%d_%H%M%S")
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_md_path = archive_dir / f"{timestamp}-aigiscode-report.md"
        archive_json_path = archive_dir / f"{timestamp}-aigiscode-report.json"

    md_content = generate_markdown_report(report)
    md_path.write_text(md_content, encoding="utf-8")
    archive_md_path.write_text(md_content, encoding="utf-8")

    json_content = generate_json_report(report)
    json_path.write_text(
        json.dumps(json_content, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    archive_json_path.write_text(
        json.dumps(json_content, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _write_handoff(report, output_dir)

    return md_path, json_path


def _write_handoff(report: ReportData, output_dir: Path) -> None:
    """Write agent handoff artifacts."""
    handoff_json = output_dir / "aigiscode-handoff.json"
    handoff_md = output_dir / "aigiscode-handoff.md"
    ga = report.graph_analysis
    summary = {
        "project_path": report.project_path,
        "files_indexed": report.files_indexed,
        "symbols_extracted": report.symbols_extracted,
        "circular_dependencies": len(ga.strong_circular_dependencies)
        or len(ga.circular_dependencies),
        "god_classes": len(ga.god_classes),
        "layer_violations": len(ga.layer_violations),
        "dead_code_total": report.dead_code.total if report.dead_code else 0,
        "hardwiring_total": report.hardwiring.total if report.hardwiring else 0,
    }
    handoff_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        f"# AigisCode Handoff — {report.project_path}",
        "",
        f"- Files: {report.files_indexed}",
        f"- Cycles: {summary['circular_dependencies']}",
        f"- God classes: {summary['god_classes']}",
        f"- Layer violations: {summary['layer_violations']}",
        f"- Dead code: {summary['dead_code_total']}",
        f"- Hardwiring: {summary['hardwiring_total']}",
    ]
    handoff_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _serialize_graph_analysis(ga) -> dict:
    """Serialize GraphAnalysisResult for JSON output."""
    return {
        "node_count": ga.node_count,
        "edge_count": ga.edge_count,
        "density": ga.density,
        "circular_dependencies": [
            {"cycle": cycle} for cycle in ga.circular_dependencies
        ],
        "strong_circular_dependencies": [
            {"cycle": cycle} for cycle in ga.strong_circular_dependencies
        ],
        "coupling_metrics": [
            {
                "module": m.module,
                "afferent": m.afferent,
                "efferent": m.efferent,
                "instability": m.instability,
            }
            for m in ga.coupling_metrics
        ],
        "god_classes": [
            {
                "name": g.name,
                "file": g.file_path,
                "methods": g.method_count,
                "dependencies": g.dependency_count,
                "lines": g.line_count,
            }
            for g in ga.god_classes
        ],
        "bottleneck_files": [
            {"file": path, "centrality": score} for path, score in ga.bottleneck_files
        ],
        "layer_violations": [
            {
                "source": v.source_file,
                "source_layer": v.source_layer.value,
                "target": v.target_name,
                "target_layer": v.target_layer.value,
                "violation": v.violation,
            }
            for v in ga.layer_violations
        ],
        "orphan_files": ga.orphan_files,
        "runtime_entry_candidates": ga.runtime_entry_candidates,
    }


def _serialize_dead_code(dc) -> dict:
    """Serialize DeadCodeResult for JSON output."""
    all_findings = (
        dc.unused_imports
        + dc.unused_methods
        + dc.unused_properties
        + dc.abandoned_classes
    )
    return {
        "total": dc.total,
        "unused_imports": len(dc.unused_imports),
        "unused_methods": len(dc.unused_methods),
        "unused_properties": len(dc.unused_properties),
        "abandoned_classes": len(dc.abandoned_classes),
        "findings": [
            {
                "file": f.file_path,
                "line": f.line,
                "name": f.name,
                "category": f.category,
                "confidence": f.confidence,
                "detail": f.detail,
            }
            for f in all_findings
        ],
    }


def _serialize_hardwiring(hw) -> dict:
    """Serialize HardwiringResult for JSON output."""
    all_findings = (
        hw.magic_strings
        + hw.repeated_literals
        + hw.hardcoded_entities
        + hw.hardcoded_network
        + hw.env_outside_config
    )
    return {
        "total": hw.total,
        "magic_strings": len(hw.magic_strings),
        "repeated_literals": len(hw.repeated_literals),
        "hardcoded_entities": len(hw.hardcoded_entities),
        "hardcoded_network": len(hw.hardcoded_network),
        "env_outside_config": len(hw.env_outside_config),
        "findings": [
            {
                "file": f.file_path,
                "line": f.line,
                "value": f.value,
                "category": f.category,
                "context": f.context,
                "severity": f.severity,
                "confidence": f.confidence,
                "suggestion": f.suggestion,
            }
            for f in all_findings
        ],
    }


def _serialize_review(rv) -> dict:
    """Serialize ReviewResult for JSON output."""
    return {
        "total_reviewed": rv.total_reviewed,
        "true_positives": rv.true_positives,
        "false_positives": rv.false_positives,
        "needs_context": rv.needs_context,
        "rules_generated": rv.rules_generated,
        "rules_prefiltered": rv.rules_prefiltered,
        "verdicts": [
            {
                "file": v.file_path,
                "line": v.line,
                "category": v.category,
                "name": v.name,
                "value": v.value,
                "verdict": v.verdict,
                "reason": v.reason,
            }
            for v in rv.verdicts
        ],
    }


def _auto_summary(report: ReportData) -> str:
    """Generate a basic auto-summary when Claude synthesis is not available."""
    ga = report.graph_analysis
    issues = []

    cycle_count = len(ga.strong_circular_dependencies) or len(ga.circular_dependencies)
    if cycle_count:
        issues.append(f"{cycle_count} circular dependencies")
    if ga.god_classes:
        issues.append(f"{len(ga.god_classes)} god classes")
    if ga.layer_violations:
        issues.append(f"{len(ga.layer_violations)} layer violations")
    if ga.orphan_files:
        issues.append(f"{len(ga.orphan_files)} potentially dead files")
    if ga.runtime_entry_candidates:
        issues.append(f"{len(ga.runtime_entry_candidates)} runtime entry candidates")
    if report.dead_code and report.dead_code.total:
        issues.append(f"{report.dead_code.total} dead code findings")
    if report.hardwiring and report.hardwiring.total:
        issues.append(f"{report.hardwiring.total} hardwiring issues")
    if report.review and report.review.true_positives:
        issues.append(f"{report.review.true_positives} AI-confirmed issues")

    if issues:
        issue_text = ", ".join(issues)
        coverage_text = _coverage_summary_text(report)
        return (
            f"The codebase contains {report.files_indexed} source files with "
            f"{report.symbols_extracted} symbols and {report.dependencies_found} dependencies. "
            f"Analysis found: {issue_text}. "
            f"See the detailed sections below for specifics.{coverage_text}"
        )
    else:
        coverage_text = _coverage_summary_text(report)
        return (
            f"The codebase contains {report.files_indexed} source files with "
            f"{report.symbols_extracted} symbols and {report.dependencies_found} dependencies. "
            f"No major structural issues were detected.{coverage_text}"
        )


def _coverage_summary_text(report: ReportData) -> str:
    parts: list[str] = []
    if report.unsupported_source_files:
        parts.append(
            f"{report.unsupported_source_files} unsupported source files were skipped"
        )
    if report.detector_coverage:
        parts.append(
            "detector coverage is partial ("
            + _format_detector_coverage(report.detector_coverage)
            + ")"
        )
    if not parts:
        return ""
    return " Partial coverage warning: " + "; ".join(parts) + "."


def _format_detector_coverage(detector_coverage: dict[str, list[str]]) -> str:
    return "; ".join(
        f"{detector}: {', '.join(languages)}"
        for detector, languages in sorted(detector_coverage.items())
        if languages
    )


def _generate_recommendations(report: ReportData) -> list[dict]:
    """Generate top recommendations based on analysis results."""
    recs = []
    ga = report.graph_analysis

    # Priority 1: Circular dependencies
    cycle_count = len(ga.strong_circular_dependencies) or len(ga.circular_dependencies)
    if cycle_count:
        qualifier = ""
        if len(ga.circular_dependencies) > cycle_count:
            qualifier = f" ({len(ga.circular_dependencies) - cycle_count} additional load-driven cycles omitted)"
        recs.append(
            {
                "title": "Break Circular Dependencies",
                "description": (
                    f"Found {cycle_count} dependency cycle(s){qualifier}. Circular dependencies make the codebase "
                    f"hard to test, refactor, and reason about. Start by extracting shared "
                    f"interfaces or introducing an event system to decouple the most entangled modules."
                ),
                "priority": "high",
            }
        )

    # Priority 2: God classes
    if ga.god_classes:
        worst = ga.god_classes[0]
        recs.append(
            {
                "title": "Refactor God Classes",
                "description": (
                    f"Found {len(ga.god_classes)} oversized classes. The worst offender is "
                    f"`{worst.name}` in `{worst.file_path}` with {worst.method_count} methods. "
                    f"Consider extracting responsibilities into dedicated service classes."
                ),
                "priority": "high",
            }
        )

    # Priority 3: Layer violations
    if ga.layer_violations:
        recs.append(
            {
                "title": "Fix Layer Violations",
                "description": (
                    f"Found {len(ga.layer_violations)} architectural layer violations. "
                    f"Lower layers (Models, Repositories) should not depend on higher layers "
                    f"(Controllers, Views). Introduce dependency inversion or event dispatching."
                ),
                "priority": "medium",
            }
        )

    # Priority 4: Bottlenecks
    if ga.bottleneck_files:
        worst_path, worst_score = ga.bottleneck_files[0]
        recs.append(
            {
                "title": "Reduce Coupling on Bottleneck Files",
                "description": (
                    f"The file `{worst_path}` has the highest betweenness centrality ({worst_score}), "
                    f"meaning changes to it affect the most other files. Consider breaking it into "
                    f"smaller, more focused modules."
                ),
                "priority": "medium",
            }
        )

    # Priority 5: Dead code
    if ga.orphan_files:
        recs.append(
            {
                "title": "Review Potentially Dead Code",
                "description": (
                    f"Found {len(ga.orphan_files)} files with no incoming dependencies. "
                    f"These exclude files already classified as runtime entry candidates, so the "
                    f"remaining list is a stronger signal for potentially unused code."
                ),
                "priority": "low",
            }
        )
    if ga.runtime_entry_candidates:
        recs.append(
            {
                "title": "Audit Runtime Entry Surfaces",
                "description": (
                    f"Found {len(ga.runtime_entry_candidates)} files that look like runtime entrypoints "
                    f"or loader-driven modules. Consider encoding these patterns in policy or plugins "
                    f"if they represent stable framework conventions."
                ),
                "priority": "low",
            }
        )

    # Dead code findings — adjust wording if AI review ran
    if report.dead_code and report.dead_code.total:
        dc = report.dead_code
        qualifier = ""
        if report.review and report.review.true_positives:
            confirmed_dc = sum(
                1
                for v in report.review.verdicts
                if v.verdict == "true_positive"
                and v.category
                in (
                    "unused_import",
                    "unused_method",
                    "unused_property",
                    "abandoned_class",
                )
            )
            if confirmed_dc:
                qualifier = f" ({confirmed_dc} confirmed by AI review)"
        recs.append(
            {
                "title": "Clean Up Dead Code",
                "description": (
                    f"Found {dc.total} dead code findings{qualifier}: "
                    f"{len(dc.unused_imports)} unused imports, "
                    f"{len(dc.unused_methods)} unused private methods, "
                    f"{len(dc.unused_properties)} unused properties, "
                    f"{len(dc.abandoned_classes)} abandoned classes. "
                    f"Review and remove to reduce maintenance burden."
                ),
                "priority": "medium",
            }
        )

    # Hardwiring findings — adjust wording if AI review ran
    if report.hardwiring and report.hardwiring.total:
        hw = report.hardwiring
        qualifier = ""
        if report.review and report.review.true_positives:
            confirmed_hw = sum(
                1
                for v in report.review.verdicts
                if v.verdict == "true_positive"
                and v.category
                in (
                    "magic_string",
                    "repeated_literal",
                    "hardcoded_entity",
                    "hardcoded_ip_url",
                    "env_outside_config",
                )
            )
            if confirmed_hw:
                qualifier = f" ({confirmed_hw} confirmed by AI review)"
        recs.append(
            {
                "title": "Reduce Hardwiring",
                "description": (
                    f"Found {hw.total} hardwiring issues{qualifier}: "
                    f"{len(hw.magic_strings)} magic strings, "
                    f"{len(hw.repeated_literals)} repeated literals, "
                    f"{len(hw.hardcoded_entities)} hardcoded entity references. "
                    f"Extract to constants, enums, or configuration."
                ),
                "priority": "medium",
            }
        )

        security_summary = _generate_security_summary(report)
        if security_summary["total_findings"]:
            recs.append(
                {
                    "title": "Prioritize Security Hardwiring Cleanup",
                    "description": (
                        f"Found {security_summary['total_findings']} security-sensitive hardwiring findings: "
                        f"{security_summary['hardcoded_network']} hardcoded network endpoints and "
                        f"{security_summary['env_outside_config']} environment reads outside config. "
                        "Move secrets, tokens, callback URLs, and environment access behind explicit "
                        "configuration boundaries."
                    ),
                    "priority": "high"
                    if security_summary["high_severity"]
                    else "medium",
                }
            )

    # If no issues, provide a positive recommendation
    if not recs:
        recs.append(
            {
                "title": "Maintain Current Standards",
                "description": (
                    "No major structural issues detected. Continue with current practices "
                    "and consider adding automated architecture tests to prevent regression."
                ),
                "priority": "info",
            }
        )

    return recs[:7]


def _generate_security_summary(report: ReportData) -> dict:
    if not report.hardwiring:
        return {
            "total_findings": 0,
            "hardcoded_network": 0,
            "env_outside_config": 0,
            "high_severity": 0,
            "ai_confirmed": 0,
            "top_findings": [],
        }

    findings = [
        *report.hardwiring.hardcoded_network,
        *report.hardwiring.env_outside_config,
    ]
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    sorted_findings = sorted(
        findings,
        key=lambda finding: (
            severity_rank.get(finding.severity, 3),
            finding.file_path,
            finding.line,
        ),
    )

    ai_confirmed = 0
    if report.review:
        ai_confirmed = sum(
            1
            for verdict in report.review.verdicts
            if verdict.verdict == "true_positive"
            and verdict.category in {"hardcoded_ip_url", "env_outside_config"}
        )

    return {
        "total_findings": len(findings),
        "hardcoded_network": len(report.hardwiring.hardcoded_network),
        "env_outside_config": len(report.hardwiring.env_outside_config),
        "high_severity": sum(1 for finding in findings if finding.severity == "high"),
        "ai_confirmed": ai_confirmed,
        "top_findings": [
            {
                "file": finding.file_path,
                "line": finding.line,
                "category": finding.category,
                "value": finding.value,
                "severity": finding.severity,
                "confidence": finding.confidence,
            }
            for finding in sorted_findings[:10]
        ],
    }
