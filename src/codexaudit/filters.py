"""Filtering helpers for detector outputs."""

from __future__ import annotations

from codexaudit.graph.deadcode import DeadCodeResult
from codexaudit.graph.hardwiring import HardwiringResult

_CONFIDENCE_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


def normalize_confidence(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in _CONFIDENCE_ORDER:
        return None
    return normalized


def filter_dead_code_result(
    result: DeadCodeResult,
    *,
    min_confidence: str | None = None,
    categories: set[str] | None = None,
) -> DeadCodeResult:
    selected_categories = set(categories or [])
    threshold = normalize_confidence(min_confidence)

    def include(finding) -> bool:
        if selected_categories and finding.category not in selected_categories:
            return False
        if threshold is None:
            return True
        return (
            _CONFIDENCE_ORDER.get(finding.confidence, -1)
            >= _CONFIDENCE_ORDER[threshold]
        )

    return DeadCodeResult(
        unused_imports=[f for f in result.unused_imports if include(f)],
        unused_methods=[f for f in result.unused_methods if include(f)],
        unused_properties=[f for f in result.unused_properties if include(f)],
        abandoned_classes=[f for f in result.abandoned_classes if include(f)],
    )


def filter_hardwiring_result(
    result: HardwiringResult,
    *,
    min_confidence: str | None = None,
    categories: set[str] | None = None,
) -> HardwiringResult:
    selected_categories = set(categories or [])
    threshold = normalize_confidence(min_confidence)

    def include(finding) -> bool:
        if selected_categories and finding.category not in selected_categories:
            return False
        if threshold is None:
            return True
        return (
            _CONFIDENCE_ORDER.get(finding.confidence, -1)
            >= _CONFIDENCE_ORDER[threshold]
        )

    return HardwiringResult(
        magic_strings=[f for f in result.magic_strings if include(f)],
        repeated_literals=[f for f in result.repeated_literals if include(f)],
        hardcoded_entities=[f for f in result.hardcoded_entities if include(f)],
        hardcoded_network=[f for f in result.hardcoded_network if include(f)],
        env_outside_config=[f for f in result.env_outside_config if include(f)],
    )
