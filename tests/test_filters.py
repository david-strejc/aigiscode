from __future__ import annotations

from codexaudit.filters import filter_dead_code_result, filter_hardwiring_result
from codexaudit.graph.deadcode import DeadCodeFinding, DeadCodeResult
from codexaudit.graph.hardwiring import HardwiringFinding, HardwiringResult


def test_filter_dead_code_result_by_category_and_confidence() -> None:
    result = DeadCodeResult(
        unused_imports=[
            DeadCodeFinding(
                file_path="a.php",
                line=1,
                category="unused_import",
                name="Foo",
                detail="x",
                confidence="high",
            )
        ],
        unused_properties=[
            DeadCodeFinding(
                file_path="b.php",
                line=2,
                category="unused_property",
                name="bar",
                detail="y",
                confidence="medium",
            )
        ],
    )

    filtered = filter_dead_code_result(
        result,
        min_confidence="high",
        categories={"unused_import", "unused_property"},
    )

    assert len(filtered.unused_imports) == 1
    assert filtered.unused_properties == []


def test_filter_hardwiring_result_by_category_and_confidence() -> None:
    result = HardwiringResult(
        magic_strings=[
            HardwiringFinding(
                file_path="a.php",
                line=1,
                category="magic_string",
                value="draft",
                context="x",
                severity="low",
                confidence="low",
                suggestion="x",
            )
        ],
        hardcoded_entities=[
            HardwiringFinding(
                file_path="b.php",
                line=2,
                category="hardcoded_entity",
                value="Task",
                context="y",
                severity="high",
                confidence="high",
                suggestion="y",
            )
        ],
    )

    filtered = filter_hardwiring_result(
        result,
        min_confidence="medium",
        categories={"hardcoded_entity"},
    )

    assert filtered.magic_strings == []
    assert len(filtered.hardcoded_entities) == 1
