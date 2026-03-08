"""AI synthesis module.

Uses Codex SDK as primary backend and optional Claude fallback to generate
a comprehensive architectural assessment from graph results + envelopes.
"""

from __future__ import annotations

from codexaudit.ai.backends import generate_text
from codexaudit.models import GraphAnalysisResult


SYNTHESIS_SYSTEM_PROMPT = """You are an expert software architect performing a codebase health assessment.
You will receive structured data about a codebase including:
1. Graph analysis results (circular dependencies, coupling metrics, god classes, bottlenecks, layer violations)
2. Semantic envelopes grouped by architectural layer (AI-generated summaries of each file)

Write a comprehensive but concise codebase health report. Focus on:
- Architectural strengths and weaknesses
- The most critical issues that should be addressed first
- Patterns and anti-patterns you observe across the codebase
- Concrete, actionable recommendations

Be direct and specific. Reference actual file names and classes. Use markdown formatting.
Do NOT repeat the raw data back. Synthesize it into insights."""

SYNTHESIS_USER_TEMPLATE = """## Graph Analysis Results

**Files**: {node_count} | **Dependencies**: {edge_count} | **Density**: {density}

### Strong Circular Dependencies ({cycle_count})
{cycles_text}

### Coupling Metrics (top 10 most unstable)
{coupling_text}

### God Classes ({god_count})
{god_classes_text}

### Bottleneck Files (top 10 by betweenness centrality)
{bottlenecks_text}

### Layer Violations ({violation_count})
{violations_text}

### Orphan Files ({orphan_count})
{orphans_text}

## Semantic Envelopes by Layer
{envelopes_text}

---

Please write a comprehensive codebase health assessment based on the above data.
Include:
1. Executive Summary (2-3 sentences)
2. Architecture Health Assessment
3. Code Quality Assessment
4. Top 5 Recommendations (ordered by impact)
"""


def _format_cycles(cycles: list[list[str]]) -> str:
    if not cycles:
        return "No circular dependencies detected."
    lines = []
    for i, cycle in enumerate(cycles[:20], 1):
        lines.append(f"{i}. {' -> '.join(cycle)} -> {cycle[0]}")
    if len(cycles) > 20:
        lines.append(f"... and {len(cycles) - 20} more")
    return "\n".join(lines)


def _format_coupling(metrics: list) -> str:
    if not metrics:
        return "No coupling data."
    lines = []
    for m in metrics[:10]:
        lines.append(
            f"- **{m.module}**: Ca={m.afferent}, Ce={m.efferent}, I={m.instability}"
        )
    return "\n".join(lines)


def _format_god_classes(classes: list) -> str:
    if not classes:
        return "No god classes detected."
    lines = []
    for g in classes[:15]:
        lines.append(
            f"- **{g.name}** ({g.file_path}): {g.method_count} methods, {g.dependency_count} deps, {g.line_count} lines"
        )
    return "\n".join(lines)


def _format_bottlenecks(bottlenecks: list) -> str:
    if not bottlenecks:
        return "No bottlenecks detected."
    lines = []
    for path, score in bottlenecks[:10]:
        lines.append(f"- **{path}**: centrality={score}")
    return "\n".join(lines)


def _format_violations(violations: list) -> str:
    if not violations:
        return "No layer violations detected."
    lines = []
    for v in violations[:20]:
        lines.append(
            f"- {v.source_file} ({v.source_layer.value}) -> {v.target_name} ({v.target_layer.value})"
        )
    if len(violations) > 20:
        lines.append(f"... and {len(violations) - 20} more")
    return "\n".join(lines)


def _format_orphans(orphans: list[str]) -> str:
    if not orphans:
        return "No orphan files detected."
    lines = [f"- {f}" for f in orphans[:30]]
    if len(orphans) > 30:
        lines.append(f"... and {len(orphans) - 30} more")
    return "\n".join(lines)


def _format_envelopes(envelopes_by_layer: dict[str, list[dict]]) -> str:
    if not envelopes_by_layer:
        return "No semantic envelopes available."

    sections = []
    for layer, items in sorted(envelopes_by_layer.items()):
        section = f"### {layer} ({len(items)} files)\n"
        for item in items[:10]:
            anti = ", ".join(item.get("anti_patterns", [])[:3])
            anti_text = f" | Anti-patterns: {anti}" if anti else ""
            section += f"- **{item['file_path']}**: {item['summary']}{anti_text}\n"
        if len(items) > 10:
            section += f"  ... and {len(items) - 10} more files\n"
        sections.append(section)

    return "\n".join(sections)


def build_synthesis_prompt(
    graph_result: GraphAnalysisResult,
    envelopes_by_layer: dict[str, list[dict]],
) -> str:
    """Build the full synthesis prompt from analysis results."""
    return SYNTHESIS_USER_TEMPLATE.format(
        node_count=graph_result.node_count,
        edge_count=graph_result.edge_count,
        density=graph_result.density,
        cycle_count=len(graph_result.strong_circular_dependencies)
        or len(graph_result.circular_dependencies),
        cycles_text=_format_cycles(
            graph_result.strong_circular_dependencies
            or graph_result.circular_dependencies
        ),
        coupling_text=_format_coupling(graph_result.coupling_metrics),
        god_count=len(graph_result.god_classes),
        god_classes_text=_format_god_classes(graph_result.god_classes),
        bottlenecks_text=_format_bottlenecks(graph_result.bottleneck_files),
        violation_count=len(graph_result.layer_violations),
        violations_text=_format_violations(graph_result.layer_violations),
        orphan_count=len(graph_result.orphan_files),
        orphans_text=_format_orphans(graph_result.orphan_files),
        envelopes_text=_format_envelopes(envelopes_by_layer),
    )


async def synthesize(
    graph_result: GraphAnalysisResult,
    envelopes_by_layer: dict[str, list[dict]],
    model: str = "gpt-5.3-codex",
    allow_claude_fallback: bool = True,
) -> str:
    """Run synthesis to generate an architectural assessment.

    Returns the synthesized markdown text, or an empty string if synthesis
    is not available.
    """
    user_prompt = build_synthesis_prompt(graph_result, envelopes_by_layer)
    text, _backend = await generate_text(
        system=SYNTHESIS_SYSTEM_PROMPT,
        user=user_prompt,
        model=model,
        allow_codex_cli_fallback=True,
        allow_claude_fallback=allow_claude_fallback,
        reasoning_effort="medium",
    )
    return text or ""
