"""AI-powered finding reviewer.

Classifies dead-code and hardwiring findings as true_positive, false_positive,
or needs_context using Codex as the primary backend with optional fallbacks.
Generates exclusion rules for false positives that persist across runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Any

from codexaudit.ai.backends import generate_text
from codexaudit.models import FindingVerdict, ReviewResult
from codexaudit.rules.engine import Rule

logger = logging.getLogger(__name__)

MAX_SAMPLE_PER_CATEGORY = 20
CONTEXT_LINES = 5  # lines before/after finding to include

REVIEW_SYSTEM_PROMPT = """\
You are an expert code reviewer specializing in static-analysis triage for multi-language application codebases.

Your task: classify each finding as one of:
- **true_positive**: The finding is a real issue that should be fixed.
- **false_positive**: The finding is wrong — the code is actually used/needed due to framework magic, \
dynamic dispatch, auto-discovery, or other patterns the static analyzer cannot see.
- **needs_context**: You cannot determine without more information (e.g., runtime behavior, config).

For each **false_positive**, provide a reusable exclusion rule using **structural checks** — \
predicates that query code structure rather than matching file paths.  Prefer structural checks \
over glob/substring patterns because they self-invalidate when code changes.

Available check types:

| Type | Params | What it does |
|------|--------|-------------|
| file_glob | pattern | fnmatch on finding's file_path |
| name_contains | substring | Substring match on finding name/value |
| context_contains | substring | Substring match on finding detail/context |
| source_regex | pattern | Regex on source file. {name} = finding's short name |
| inherits | ancestor | Class extends ancestor (short name, e.g. "ServiceProvider") |
| implements | interface | Class implements interface (short name) |
| referenced_as_type_hint | (none) | Class appears as constructor type hint in other files |
| file_in_layer | layer | File's architectural layer matches |

Multiple checks = AND conjunction.  For OR, create separate rules.

**Prefer structural checks** (inherits, implements, referenced_as_type_hint, source_regex) over \
simple glob/substring patterns.  A rule based on inheritance is inherently self-invalidating: if a \
class stops extending ServiceProvider, the rule stops matching.

Respond with valid JSON only. No markdown, no explanation outside JSON.

Response schema:
{
  "verdicts": [
    {
      "index": 0,
      "verdict": "true_positive|false_positive|needs_context",
      "reason": "brief explanation",
      "rule_checks": [{"type": "inherits", "params": {"ancestor": "Model"}}],
      "rule_pattern": null
    }
  ]
}

Use rule_checks (array of check objects) for new rules.  rule_pattern is kept for backward \
compatibility only — prefer rule_checks.
"""


def _read_code_context(project_path: Path, file_path: str, line: int) -> str:
    """Read +-CONTEXT_LINES around a finding from the source file."""
    full_path = project_path / file_path
    if not full_path.exists():
        return ""
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line - CONTEXT_LINES - 1)
        end = min(len(lines), line + CONTEXT_LINES)
        numbered = []
        for i, ln in enumerate(lines[start:end], start=start + 1):
            marker = ">>>" if i == line else "   "
            numbered.append(f"{marker} {i:4d} | {ln}")
        return "\n".join(numbered)
    except Exception:
        return ""


def _get_structural_context(finding: Any, store: Any) -> dict[str, Any]:
    """Query IndexStore for structural metadata about a finding."""
    info: dict[str, Any] = {}
    if store is None:
        return info

    file_info = store.get_file_by_path(finding.file_path)
    if file_info is None:
        return info

    deps = store.get_dependencies_for_file(file_info.id)
    inherits = [d.target_name for d in deps if d.type.value == "inherit"]
    implements = [d.target_name for d in deps if d.type.value == "implement"]
    imports = [d.target_name for d in deps if d.type.value == "import"]

    if inherits:
        info["extends"] = inherits
    if implements:
        info["implements"] = implements
    if imports:
        info["import_count"] = len(imports)

    # Check layer from envelope
    try:
        row = store.conn.execute(
            "SELECT architectural_layer FROM envelopes WHERE file_id = ?",
            (file_info.id,),
        ).fetchone()
        if row:
            info["layer"] = row["architectural_layer"]
    except Exception:
        pass

    return info


def _format_finding_for_prompt(
    finding: Any,
    idx: int,
    project_path: Path,
    finding_type: str,
    store: Any = None,
) -> str:
    """Format a single finding with code context for the AI prompt."""
    parts = [f"### Finding #{idx}"]

    parts.append(f"**File**: `{finding.file_path}:{finding.line}`")
    parts.append(f"**Category**: `{finding.category}`")

    if finding_type == "dead_code":
        parts.append(f"**Symbol**: `{finding.name}`")
        parts.append(f"**Detail**: {finding.detail}")
        parts.append(f"**Confidence**: {finding.confidence}")
    else:
        parts.append(f"**Value**: `{finding.value}`")
        parts.append(f"**Severity**: {finding.severity}")
        if getattr(finding, "confidence", ""):
            parts.append(f"**Confidence**: {finding.confidence}")
        parts.append(f"**Suggestion**: {finding.suggestion}")
        if finding.context:
            parts.append(f"**Context snippet**: `{finding.context[:100]}`")

    code = _read_code_context(project_path, finding.file_path, finding.line)
    if code:
        parts.append(f"**Source code**:\n```\n{code}\n```")

    # Structural context from IndexStore
    struct = _get_structural_context(finding, store)
    if struct:
        ctx_parts = []
        if "extends" in struct:
            ctx_parts.append(f"Extends: {', '.join(struct['extends'])}")
        if "implements" in struct:
            ctx_parts.append(f"Implements: {', '.join(struct['implements'])}")
        if "layer" in struct:
            ctx_parts.append(f"Layer: {struct['layer']}")
        if "import_count" in struct:
            ctx_parts.append(
                f"Imported by others: {struct['import_count']} imports in file"
            )
        if ctx_parts:
            parts.append(f"**Structural context**: {'; '.join(ctx_parts)}")

    return "\n".join(parts)


def _sample_findings(findings: list, max_n: int = MAX_SAMPLE_PER_CATEGORY) -> list:
    """Sample up to max_n findings, preferring diverse file paths."""
    if len(findings) <= max_n:
        return list(findings)

    # Group by directory to get diversity
    by_dir: dict[str, list] = {}
    for f in findings:
        d = str(Path(f.file_path).parent)
        by_dir.setdefault(d, []).append(f)

    sampled = []
    dirs = list(by_dir.keys())
    random.shuffle(dirs)

    # Round-robin from each directory
    idx = 0
    while len(sampled) < max_n:
        d = dirs[idx % len(dirs)]
        bucket = by_dir[d]
        if bucket:
            sampled.append(bucket.pop(0))
        else:
            dirs.remove(d)
            if not dirs:
                break
        idx += 1

    return sampled


def _group_findings_by_category(
    dead_code: Any,
    hardwiring: Any,
) -> dict[str, tuple[list, str]]:
    """Group all findings by category. Returns {category: (findings_list, finding_type)}."""
    groups: dict[str, tuple[list, str]] = {}

    if dead_code:
        for attr in [
            "unused_imports",
            "unused_methods",
            "unused_properties",
            "abandoned_classes",
        ]:
            findings = getattr(dead_code, attr, [])
            if findings:
                cat = findings[0].category
                groups[cat] = (findings, "dead_code")

    if hardwiring:
        for attr in [
            "magic_strings",
            "repeated_literals",
            "hardcoded_entities",
            "hardcoded_network",
            "env_outside_config",
        ]:
            findings = getattr(hardwiring, attr, [])
            if findings:
                cat = findings[0].category
                groups[cat] = (findings, "hardwiring")

    return groups


def _build_batch_prompt(
    category: str,
    total_count: int,
    sampled: list,
    finding_type: str,
    project_path: Path,
    project_type: str,
    store: Any = None,
) -> str:
    """Build the user prompt for one category batch."""
    parts = [
        f"## Project Type: {project_type}",
        f"## Category: `{category}` ({total_count} total findings, {len(sampled)} sampled)",
        "",
    ]
    for i, finding in enumerate(sampled):
        parts.append(
            _format_finding_for_prompt(
                finding, i, project_path, finding_type, store=store
            )
        )
        parts.append("")

    parts.append(
        f"Classify each finding (indices 0-{len(sampled) - 1}) as true_positive, "
        "false_positive, or needs_context. For false_positives, provide structural "
        "rule_checks that would match ALL similar false positives in this category. "
        "Prefer inherits/implements/referenced_as_type_hint/source_regex checks over "
        "simple glob/substring patterns."
    )

    return "\n".join(parts)


def _make_rule_id(category: str, pattern: str | dict) -> str:
    """Generate a deterministic rule ID from category + pattern."""
    raw = f"{category}:{json.dumps(pattern, sort_keys=True)}"
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"rule-{h}"


def _parse_ai_response(
    response_text: str,
    sampled: list,
    category: str,
    finding_type: str,
) -> tuple[list[FindingVerdict], list[Rule]]:
    """Parse AI response JSON into verdicts and rules."""
    verdicts: list[FindingVerdict] = []
    rules: list[Rule] = []

    try:
        # Handle markdown-wrapped JSON
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse AI response as JSON for category %s", category)
        return verdicts, rules

    for v in data.get("verdicts", []):
        idx = v.get("index", -1)
        if idx < 0 or idx >= len(sampled):
            continue

        finding = sampled[idx]
        verdict = FindingVerdict(
            file_path=finding.file_path,
            line=finding.line,
            category=category,
            name=getattr(finding, "name", ""),
            value=getattr(finding, "value", ""),
            verdict=v.get("verdict", "needs_context"),
            reason=v.get("reason", ""),
        )
        verdicts.append(verdict)

        # Generate rule for false positives
        if verdict.verdict == "false_positive":
            rule_checks = v.get("rule_checks")
            rule_pattern = v.get("rule_pattern")

            if rule_checks and isinstance(rule_checks, list):
                # v2 structural checks (preferred)
                rule = Rule(
                    id=_make_rule_id(category, rule_checks),
                    category=category,
                    checks=rule_checks,
                    reason=v.get("reason", ""),
                    created_by="ai",
                    status="probationary",
                )
                rules.append(rule)
            elif rule_pattern:
                # v1 pattern — convert to v2 checks
                from codexaudit.rules.engine import _migrate_v1_pattern

                checks = _migrate_v1_pattern(rule_pattern)
                rule = Rule(
                    id=_make_rule_id(category, rule_pattern),
                    category=category,
                    checks=checks,
                    reason=v.get("reason", ""),
                    created_by="ai",
                    status="probationary",
                )
                rules.append(rule)

    return verdicts, rules


async def review_findings(
    dead_code: Any,
    hardwiring: Any,
    project_path: Path,
    project_type: str = "mixed-language project",
    store: Any = None,
    review_model: str = "gpt-5.3-codex",
    allow_claude_fallback: bool = True,
) -> tuple[ReviewResult, list[Rule]]:
    """Review all findings using AI, returning verdicts and generated rules.

    Groups findings by category, samples up to 20 per category, and makes
    one API call per category (max 9 calls total).
    """
    groups = _group_findings_by_category(dead_code, hardwiring)

    all_verdicts: list[FindingVerdict] = []
    all_rules: list[Rule] = []
    total_reviewed = 0

    for category, (findings, finding_type) in groups.items():
        sampled = _sample_findings(findings)
        total_reviewed += len(sampled)

        prompt = _build_batch_prompt(
            category=category,
            total_count=len(findings),
            sampled=sampled,
            finding_type=finding_type,
            project_path=project_path,
            project_type=project_type,
            store=store,
        )

        response, backend = await generate_text(
            REVIEW_SYSTEM_PROMPT,
            prompt,
            model=review_model,
            allow_codex_cli_fallback=True,
            allow_claude_fallback=allow_claude_fallback,
            reasoning_effort="medium",
        )
        if not response:
            logger.warning(
                "No AI response for category %s, marking all as needs_context", category
            )
            for f in sampled:
                all_verdicts.append(
                    FindingVerdict(
                        file_path=f.file_path,
                        line=f.line,
                        category=category,
                        name=getattr(f, "name", ""),
                        value=getattr(f, "value", ""),
                        verdict="needs_context",
                        reason="AI review unavailable",
                    )
                )
            continue
        logger.debug("Reviewed category '%s' via backend %s", category, backend)

        verdicts, rules = _parse_ai_response(response, sampled, category, finding_type)
        all_verdicts.extend(verdicts)
        all_rules.extend(rules)

    tp = sum(1 for v in all_verdicts if v.verdict == "true_positive")
    fp = sum(1 for v in all_verdicts if v.verdict == "false_positive")
    nc = sum(1 for v in all_verdicts if v.verdict == "needs_context")

    result = ReviewResult(
        total_reviewed=total_reviewed,
        true_positives=tp,
        false_positives=fp,
        needs_context=nc,
        rules_generated=len(all_rules),
        verdicts=all_verdicts,
    )

    return result, all_rules
