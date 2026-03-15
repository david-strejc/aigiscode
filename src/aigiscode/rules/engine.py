"""Rules engine for self-learning false positive exclusion (v2).

Loads, matches, and persists exclusion rules that prevent known false
positives from being re-reviewed by the AI reviewer on subsequent runs.

v2 rules use **structural checks** — predicate functions that query the
IndexStore at evaluation time.  v1 rules (glob/substring patterns) are
auto-migrated to v2 on load.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aigiscode.rules.checks import StructuralContext, run_checks

logger = logging.getLogger(__name__)

# Dead code category list names on DeadCodeResult
DC_LISTS = [
    "unused_imports",
    "unused_methods",
    "unused_properties",
    "abandoned_classes",
]
# Hardwiring category list names on HardwiringResult
HW_LISTS = [
    "magic_strings",
    "repeated_literals",
    "hardcoded_entities",
    "hardcoded_network",
    "env_outside_config",
]


# ---------------------------------------------------------------------------
# Rule dataclass
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A single exclusion rule (v2 format)."""

    id: str
    category: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    created_by: str = "ai"
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    status: str = "active"  # probationary | active | stale | disabled
    hit_count: int = 0
    last_hit_run: str = ""
    miss_streak: int = 0

    # Legacy field — only used during v1→v2 migration
    pattern: str | dict[str, str] | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Seed rules
# ---------------------------------------------------------------------------

SEED_RULES = [
    Rule(
        id="seed-sp-inherit",
        category="abandoned_class",
        checks=[{"type": "inherits", "params": {"ancestor": "ServiceProvider"}}],
        reason="Laravel ServiceProviders auto-discovered at runtime",
        created_by="seed",
    ),
    Rule(
        id="seed-attr-import",
        category="unused_import",
        checks=[{"type": "source_regex", "params": {"pattern": r"#\[{name}[\s(]"}}],
        reason="PHP attribute imports consumed via #[ClassName] syntax",
        created_by="seed",
    ),
    Rule(
        id="seed-di-typehint",
        category="abandoned_class",
        checks=[{"type": "referenced_as_type_hint", "params": {}}],
        reason="Class injected via constructor type hints (DI container)",
        created_by="seed",
    ),
]


def ensure_seed_rules(path: Path) -> int:
    """Create seed rules if the rules file doesn't exist yet.

    Returns the number of seed rules added (0 if file already exists).
    """
    if path.exists():
        return 0

    save_rules(path, list(SEED_RULES))
    logger.info("Created %d seed rules at %s", len(SEED_RULES), path)
    return len(SEED_RULES)


# ---------------------------------------------------------------------------
# v1 → v2 migration
# ---------------------------------------------------------------------------


def _migrate_v1_pattern(pattern: str | dict[str, str]) -> list[dict[str, Any]]:
    """Convert a v1 pattern (glob string or dict) into a list of v2 checks."""
    if isinstance(pattern, str):
        return [{"type": "file_glob", "params": {"pattern": pattern}}]
    elif isinstance(pattern, dict):
        checks = []
        if "context_contains" in pattern:
            checks.append(
                {
                    "type": "context_contains",
                    "params": {"substring": pattern["context_contains"]},
                }
            )
        if "name_contains" in pattern:
            checks.append(
                {
                    "type": "name_contains",
                    "params": {"substring": pattern["name_contains"]},
                }
            )
        return checks if checks else [{"type": "file_glob", "params": {"pattern": "*"}}]
    return []


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------


def load_rules(path: Path) -> list[Rule]:
    """Load rules from JSON file.  Auto-migrates v1 → v2.

    Returns empty list on missing/corrupt file.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("version", 1)
        rules: list[Rule] = []

        for r in data.get("rules", []):
            if version < 2 or "pattern" in r and "checks" not in r:
                # v1 rule — migrate
                checks = _migrate_v1_pattern(r["pattern"])
                rule = Rule(
                    id=r["id"],
                    category=r["category"],
                    checks=checks,
                    reason=r.get("reason", ""),
                    created_by=r.get("created_by", "unknown"),
                    created_at=r.get("created_at", ""),
                    status=r.get("status", "active"),
                )
            else:
                rule = Rule(
                    id=r["id"],
                    category=r["category"],
                    checks=r.get("checks", []),
                    reason=r.get("reason", ""),
                    created_by=r.get("created_by", "unknown"),
                    created_at=r.get("created_at", ""),
                    status=r.get("status", "active"),
                    hit_count=r.get("hit_count", 0),
                    last_hit_run=r.get("last_hit_run", ""),
                    miss_streak=r.get("miss_streak", 0),
                )
            rules.append(rule)

        if version < 2 and rules:
            logger.info("Auto-migrated %d v1 rules to v2 format", len(rules))
            save_rules(path, rules)

        return rules
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to load rules from %s: %s", path, e)
        return []


def _rule_to_dict(r: Rule) -> dict[str, Any]:
    """Serialize a Rule to a JSON-safe dict (v2 format, no legacy fields)."""
    return {
        "id": r.id,
        "category": r.category,
        "checks": r.checks,
        "reason": r.reason,
        "created_by": r.created_by,
        "created_at": r.created_at,
        "status": r.status,
        "hit_count": r.hit_count,
        "last_hit_run": r.last_hit_run,
        "miss_streak": r.miss_streak,
    }


def save_rules(path: Path, rules: list[Rule]) -> None:
    """Atomic write: write to temp file then rename.  Always writes v2."""
    data = {
        "version": 2,
        "rules": [_rule_to_dict(r) for r in rules],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_rules(path: Path, new_rules: list[Rule]) -> int:
    """Load existing rules, deduplicate, save.  Returns count added."""
    existing = load_rules(path)
    existing_ids = {r.id for r in existing}

    added = 0
    for rule in new_rules:
        if rule.id in existing_ids:
            continue
        # Deduplicate by (category, checks) combo
        if any(
            r.category == rule.category and r.checks == rule.checks for r in existing
        ):
            continue
        existing.append(rule)
        existing_ids.add(rule.id)
        added += 1

    if added > 0:
        save_rules(path, existing)
    return added


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def matches_rule(
    finding: Any, rule: Rule, ctx: StructuralContext | None = None
) -> bool:
    """Check if a finding matches a rule using v2 structural checks.

    Disabled rules never match.  If no StructuralContext is provided,
    a bare context (no store, no project_root) is used — structural
    checks will fail open.
    """
    if rule.status == "disabled":
        return False

    if finding.category != rule.category:
        return False

    if ctx is None:
        ctx = StructuralContext()

    return run_checks(finding, rule.checks, ctx)


# ---------------------------------------------------------------------------
# Filtering with stats tracking
# ---------------------------------------------------------------------------


def _filter_list(
    findings: list,
    rules: list[Rule],
    ctx: StructuralContext,
    hit_rules: set[str],
) -> tuple[list, int]:
    """Filter a list of findings, tracking which rules fired."""
    if not rules:
        return findings, 0

    kept = []
    excluded = 0
    for f in findings:
        matched = False
        for r in rules:
            if matches_rule(f, r, ctx):
                hit_rules.add(r.id)
                matched = True
                break
        if matched:
            excluded += 1
        else:
            kept.append(f)
    return kept, excluded


def filter_findings(
    dead_code: Any,
    hardwiring: Any,
    rules: list[Rule],
    ctx: StructuralContext | None = None,
    run_id: str = "",
) -> tuple[Any, Any, int]:
    """Remove findings matching exclusion rules.

    Returns (filtered_dead_code, filtered_hardwiring, total_excluded_count).
    Does NOT mutate the originals — returns new result objects.
    Also updates rule stats (hit_count, miss_streak) in-place.
    """
    from aigiscode.graph.deadcode import DeadCodeResult
    from aigiscode.graph.hardwiring import HardwiringResult

    if ctx is None:
        ctx = StructuralContext()

    hit_rules: set[str] = set()
    total_excluded = 0

    # Filter dead code
    dc_kwargs = {}
    for attr in DC_LISTS:
        original = getattr(dead_code, attr, [])
        kept, excluded = _filter_list(original, rules, ctx, hit_rules)
        dc_kwargs[attr] = kept
        total_excluded += excluded
    filtered_dc = DeadCodeResult(**dc_kwargs)

    # Filter hardwiring
    hw_kwargs = {}
    for attr in HW_LISTS:
        original = getattr(hardwiring, attr, [])
        kept, excluded = _filter_list(original, rules, ctx, hit_rules)
        hw_kwargs[attr] = kept
        total_excluded += excluded
    filtered_hw = HardwiringResult(**hw_kwargs)

    # Update stats
    if run_id:
        update_rule_stats(rules, hit_rules, run_id)

    return filtered_dc, filtered_hw, total_excluded


# ---------------------------------------------------------------------------
# Rule lifecycle / stats
# ---------------------------------------------------------------------------


def update_rule_stats(rules: list[Rule], hit_rules: set[str], run_id: str) -> None:
    """Update hit counts and staleness for all rules after a filtering pass.

    Lifecycle transitions:
        probationary → active     (2+ runs with hits)
        active       → stale      (5 consecutive misses)
        stale        → disabled   (8 consecutive misses)
        any          → active     (hit resets miss_streak)
    """
    for rule in rules:
        if rule.status == "disabled":
            continue

        if rule.id in hit_rules:
            rule.hit_count += 1
            rule.last_hit_run = run_id
            rule.miss_streak = 0

            # Promote probationary → active after 2+ hits
            if rule.status == "probationary" and rule.hit_count >= 2:
                rule.status = "active"
                logger.info(
                    "Rule %s promoted to active (hit_count=%d)", rule.id, rule.hit_count
                )

            # Revive stale → active on hit
            if rule.status == "stale":
                rule.status = "active"
                logger.info("Rule %s revived to active", rule.id)
        else:
            rule.miss_streak += 1

            if rule.miss_streak >= 8 and rule.status in ("stale", "active"):
                rule.status = "disabled"
                logger.info(
                    "Rule %s disabled (miss_streak=%d)", rule.id, rule.miss_streak
                )
            elif rule.miss_streak >= 5 and rule.status == "active":
                rule.status = "stale"
                logger.info(
                    "Rule %s marked stale (miss_streak=%d)", rule.id, rule.miss_streak
                )


# ---------------------------------------------------------------------------
# External findings filtering
# ---------------------------------------------------------------------------


def filter_external_findings(
    external_analysis: Any,
    rules: list[Rule],
    ctx: Any = None,
) -> tuple[Any, int]:
    """Filter external security findings against saved rules.

    Accepts either an ``ExternalAnalysisResult`` or a plain list of findings
    (for backward compatibility).

    When given an ``ExternalAnalysisResult``, returns a new
    ``(filtered_ExternalAnalysisResult, excluded_count)`` tuple with
    tool run summaries updated to reflect post-filtering counts.

    When given a plain list, returns ``(filtered_list, excluded_count)``.
    """
    from aigiscode.models import ExternalAnalysisResult, ExternalToolRun

    # Handle plain list (backward compat)
    if isinstance(external_analysis, list):
        if not rules or not external_analysis:
            return external_analysis, 0
        if ctx is None:
            ctx = StructuralContext()
        kept = []
        excluded = 0
        for finding in external_analysis:
            matched = False
            for rule in rules:
                if _plain_finding_matches(finding, rule, ctx):
                    matched = True
                    break
            if matched:
                excluded += 1
            else:
                kept.append(finding)
        return kept, excluded

    # Handle ExternalAnalysisResult
    if not rules or not external_analysis.findings:
        return external_analysis, 0

    if ctx is None:
        ctx = StructuralContext()

    hit_rules: set[str] = set()
    kept: list = []
    excluded = 0

    for finding in external_analysis.findings:
        matched = False
        for rule in rules:
            if matches_rule(finding, rule, ctx):
                hit_rules.add(rule.id)
                matched = True
                break
        if matched:
            excluded += 1
        else:
            kept.append(finding)

    # Rebuild tool_runs with updated summaries
    updated_tool_runs: list[ExternalToolRun] = []
    for tr in external_analysis.tool_runs:
        tool_findings = [f for f in kept if f.tool == tr.tool]
        new_summary = dict(tr.summary)
        new_summary["finding_count"] = len(tool_findings)
        new_summary["rules_filtered_count"] = excluded
        updated_tool_runs.append(
            ExternalToolRun(
                tool=tr.tool,
                command=tr.command,
                status=tr.status,
                findings_count=len(tool_findings),
                summary=new_summary,
                version=tr.version,
            )
        )

    return ExternalAnalysisResult(
        tool_runs=updated_tool_runs,
        findings=kept,
    ), excluded


def _plain_finding_matches(finding: Any, rule: Rule, ctx: StructuralContext) -> bool:
    """Try to match a plain dict or object against a rule.

    Plain dicts don't have a ``category`` attribute so this always returns
    False — preserving the old pass-through behaviour.
    """
    if isinstance(finding, dict):
        return False
    return matches_rule(finding, rule, ctx)
