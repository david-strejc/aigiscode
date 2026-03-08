"""Structural check predicates for false positive exclusion.

Each check is a predicate function that receives a finding, a rule's check
params, and a StructuralContext.  It returns True when the finding matches
the structural pattern described by the check (i.e. should be suppressed).

Structural checks that need IndexStore data **fail open** — they return False
(do not suppress) when the store is unavailable, ensuring no false negatives.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CheckFn = Callable[[Any, dict[str, Any], "StructuralContext"], bool]
CHECK_REGISTRY: dict[str, CheckFn] = {}


def register_check(name: str) -> Callable[[CheckFn], CheckFn]:
    """Decorator to register a check function by type name."""

    def decorator(fn: CheckFn) -> CheckFn:
        CHECK_REGISTRY[name] = fn
        return fn

    return decorator


@dataclass
class StructuralContext:
    """Runtime context passed to every check function.

    Holds a reference to the IndexStore (may be None for --skip-ai runs)
    and the project root for source file reads.  Caches file contents to
    avoid redundant I/O within a single filtering pass.
    """

    store: Any | None = None  # IndexStore
    project_root: Path | None = None
    _file_cache: dict[str, str | None] = field(default_factory=dict, repr=False)

    def read_file(self, rel_path: str) -> str | None:
        """Read a source file by project-relative path, with caching."""
        if rel_path in self._file_cache:
            return self._file_cache[rel_path]

        if self.project_root is None:
            self._file_cache[rel_path] = None
            return None

        full = self.project_root / rel_path
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            content = None

        self._file_cache[rel_path] = content
        return content


# ---------------------------------------------------------------------------
# Pattern checks (work without IndexStore)
# ---------------------------------------------------------------------------


@register_check("file_glob")
def check_file_glob(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """fnmatch on the finding's file_path."""
    pattern = params.get("pattern", "")
    return bool(pattern and fnmatch(finding.file_path, pattern))


@register_check("name_contains")
def check_name_contains(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Substring match on finding name/value."""
    substring = params.get("substring", "")
    if not substring:
        return False
    name = getattr(finding, "name", "") or getattr(finding, "value", "")
    return substring in name


@register_check("context_contains")
def check_context_contains(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Substring match on finding detail/context."""
    substring = params.get("substring", "")
    if not substring:
        return False
    text = getattr(finding, "context", "") or getattr(finding, "detail", "")
    return substring in text


@register_check("source_regex")
def check_source_regex(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Regex match against the source file content.

    The placeholder ``{name}`` in the pattern is replaced with the finding's
    short class/symbol name (last segment after ``\\``).
    """
    raw_pattern = params.get("pattern", "")
    if not raw_pattern:
        return False

    # Resolve {name} placeholder
    full_name = getattr(finding, "name", "") or getattr(finding, "value", "")
    short_name = full_name.rsplit("\\", 1)[-1] if full_name else ""
    pattern = raw_pattern.replace("{name}", re.escape(short_name))

    source = ctx.read_file(finding.file_path)
    if source is None:
        return False

    try:
        return bool(re.search(pattern, source))
    except re.error:
        logger.warning("Invalid regex in source_regex check: %s", raw_pattern)
        return False


# ---------------------------------------------------------------------------
# Structural checks (require IndexStore — fail open without it)
# ---------------------------------------------------------------------------


@register_check("inherits")
def check_inherits(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Class extends a given ancestor (queries dependencies WHERE type='inherit')."""
    if ctx.store is None:
        return False

    ancestor = params.get("ancestor", "")
    if not ancestor:
        return False

    # Find the file in the store
    file_info = ctx.store.get_file_by_path(finding.file_path)
    if file_info is None:
        return False

    # Check if any inherit dependency in this file targets the ancestor
    deps = ctx.store.get_dependencies_for_file(file_info.id)
    for dep in deps:
        if dep.type.value == "inherit":
            # Match short name: "ServiceProvider" matches "Illuminate\...\ServiceProvider"
            target_short = dep.target_name.rsplit("\\", 1)[-1]
            if target_short == ancestor or dep.target_name == ancestor:
                return True

    return False


@register_check("implements")
def check_implements(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Class implements a given interface (queries dependencies WHERE type='implement')."""
    if ctx.store is None:
        return False

    interface = params.get("interface", "")
    if not interface:
        return False

    file_info = ctx.store.get_file_by_path(finding.file_path)
    if file_info is None:
        return False

    deps = ctx.store.get_dependencies_for_file(file_info.id)
    for dep in deps:
        if dep.type.value == "implement":
            target_short = dep.target_name.rsplit("\\", 1)[-1]
            if target_short == interface or dep.target_name == interface:
                return True

    return False


@register_check("referenced_as_type_hint")
def check_referenced_as_type_hint(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """Class appears as a constructor type hint in other files.

    Searches all dependencies for import references to this class's short name
    that come from files other than the class's own file.
    """
    if ctx.store is None:
        return False

    full_name = getattr(finding, "name", "") or ""
    short_name = full_name.rsplit("\\", 1)[-1] if full_name else ""
    if not short_name:
        return False

    # Query: is this class name imported by other files?
    try:
        rows = ctx.store.conn.execute(
            """
            SELECT COUNT(*) as cnt FROM dependencies d
            JOIN files f ON f.id = d.source_file_id
            WHERE d.target_name LIKE ? AND d.type = 'import'
            AND f.path != ?
            """,
            (f"%{short_name}", finding.file_path),
        ).fetchone()
        return rows["cnt"] > 0
    except Exception:
        return False


@register_check("file_in_layer")
def check_file_in_layer(
    finding: Any, params: dict[str, Any], ctx: StructuralContext
) -> bool:
    """File's architectural layer matches (via envelope or path heuristic)."""
    layer = params.get("layer", "")
    if not layer:
        return False

    # Try envelope first
    if ctx.store is not None:
        file_info = ctx.store.get_file_by_path(finding.file_path)
        if file_info is not None:
            try:
                row = ctx.store.conn.execute(
                    "SELECT architectural_layer FROM envelopes WHERE file_id = ?",
                    (file_info.id,),
                ).fetchone()
                if row and row["architectural_layer"].lower() == layer.lower():
                    return True
            except Exception:
                pass

    # Path heuristic fallback
    path_lower = finding.file_path.lower()
    return layer.lower() in path_lower


def run_checks(
    finding: Any, checks: list[dict[str, Any]], ctx: StructuralContext
) -> bool:
    """Run all checks for a rule against a finding.

    Returns True only if ALL checks pass (AND conjunction).
    Unknown check types are treated as failures (non-match).
    """
    for check in checks:
        check_type = check.get("type", "")
        check_params = check.get("params", {})

        fn = CHECK_REGISTRY.get(check_type)
        if fn is None:
            logger.warning("Unknown check type '%s', treating as non-match", check_type)
            return False

        if not fn(finding, check_params, ctx):
            return False

    return True
