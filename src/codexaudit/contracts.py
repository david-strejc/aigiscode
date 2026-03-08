"""Shared extraction of declared runtime contracts."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Iterable

from codexaudit.indexer.store import IndexStore

_MAX_SAMPLE_LOCATIONS = 5
_MAX_ITEMS_PER_CATEGORY = 50
_SUPPORTED_LANGUAGES = {"php", "python", "ruby", "javascript", "typescript", "vue"}

_ROUTE_PATTERNS = (
    re.compile(
        r"\b(?:Route|Router)(?:::|->)(?:get|post|put|patch|delete|options|any|match|resource|apiResource|view|redirect|prefix|group)\s*\(\s*['\"](?P<value>/[^'\"]*)['\"]"
    ),
    re.compile(
        r"\brouter\.(?:get|post|put|patch|delete|use|all)\s*\(\s*['\"`](?P<value>/[^'\"`]*)['\"`]"
    ),
    re.compile(
        r"\b(?:get|post|put|patch|delete|match)\s+['\"](?P<value>/[^'\"]*)['\"]"
    ),
    re.compile(r"\bmount\s+[A-Z][A-Za-z0-9_:]+\s*=>\s*['\"](?P<value>/[^'\"]*)['\"]"),
)

_HOOK_PATTERNS = (
    re.compile(
        r"\b(?:add_action|add_filter|do_action|apply_filters|register_activation_hook|register_deactivation_hook|register_uninstall_hook)\s*\(\s*['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]"
    ),
)

_REGISTERED_KEY_PATTERNS = (
    re.compile(
        r"\b(?:register_[A-Za-z0-9_]+|bind|singleton|alias|command|schedule)\s*\(\s*['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]",
        re.IGNORECASE,
    ),
)
_SYMBOLIC_LITERAL_VALUE = r"[A-Za-z0-9_.:/-]+"
_TS_LITERAL_UNION_PATTERNS = (
    re.compile(
        rf"\b(?:export\s+)?type\s+[A-Za-z0-9_<>,\s]+\s*=\s*(?P<body>(?:['\"]{_SYMBOLIC_LITERAL_VALUE}['\"]\s*\|\s*)+['\"]{_SYMBOLIC_LITERAL_VALUE}['\"])",
        re.MULTILINE,
    ),
    re.compile(
        rf"[(:]\s*(?P<body>(?:['\"]{_SYMBOLIC_LITERAL_VALUE}['\"]\s*\|\s*)+['\"]{_SYMBOLIC_LITERAL_VALUE}['\"])\s*(?:[;,\)])",
        re.MULTILINE,
    ),
)
_TS_CONST_ARRAY_PATTERN = re.compile(
    rf"\[(?P<body>(?:\s*['\"]{_SYMBOLIC_LITERAL_VALUE}['\"]\s*,?)+)\]\s+as\s+const",
    re.MULTILINE,
)
_PHP_REGISTER_ARRAY_PATTERN = re.compile(
    r"\bregister_[A-Za-z0-9_]+\s*\(\s*array\s*\((?P<body>[\s\S]{0,2000}?)\)\s*\)",
    re.IGNORECASE,
)
_ARRAY_KEY_PATTERN = re.compile(r"['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]\s*=>")
_QUOTED_VALUE_PATTERN = re.compile(r"['\"](?P<value>[A-Za-z0-9_.:/-]+)['\"]")
_CUSTOM_EVENT_PATTERN = re.compile(
    r"\bnew\s+CustomEvent\s*\(\s*['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]"
)
_DATA_CONTROLLER_PATTERN = re.compile(
    r"""data-controller\s*=\s*['"](?P<value>[^'"]+)['"]""",
    re.IGNORECASE,
)
_DATA_ACTION_PATTERN = re.compile(
    r"""data-action\s*=\s*['"](?P<value>[^'"]+)['"]""",
    re.IGNORECASE,
)
_ACTION_CONTROLLER_PATTERN = re.compile(r"(?:->)?(?P<value>[A-Za-z0-9_-]+)#")

_ENV_PATTERNS = (
    re.compile(r"(?<![\w.])env\s*\(\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"(?<![\w.])getenv\s*\(\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"\$_(?:ENV|SERVER)\s*\[\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]\s*\]"),
    re.compile(r"\bos\.getenv\s*\(\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"\bos\.environ(?:\.get)?\s*\(\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"\bos\.environ\s*\[\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]\s*\]"),
    re.compile(r"\bENV\.fetch\s*\(\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"\bENV\s*\[\s*['\"](?P<value>[A-Z][A-Z0-9_]+)['\"]\s*\]"),
    re.compile(r"\bprocess\.env\.(?P<value>[A-Z][A-Z0-9_]*)"),
    re.compile(r"\bprocess\.env\s*\[\s*['\"](?P<value>[A-Z][A-Z0-9_]*)['\"]\s*\]"),
    re.compile(r"\bimport\.meta\.env\.(?P<value>[A-Z][A-Z0-9_]*)"),
)

_CONFIG_PATTERNS = (
    re.compile(r"\bconfig\s*\(\s*['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]"),
    re.compile(
        r"\b(?:get_option|update_option|add_option|delete_option|register_setting)\s*\(\s*['\"](?P<value>[A-Za-z0-9_.:-]+)['\"]"
    ),
)

_PATTERN_MAP: dict[str, tuple[re.Pattern[str], ...]] = {
    "routes": _ROUTE_PATTERNS,
    "hooks": _HOOK_PATTERNS,
    "registered_keys": _REGISTERED_KEY_PATTERNS,
    "env_keys": _ENV_PATTERNS,
    "config_keys": _CONFIG_PATTERNS,
}


@dataclass(frozen=True)
class ContractLookup:
    routes: frozenset[str] = frozenset()
    hooks: frozenset[str] = frozenset()
    registered_keys: frozenset[str] = frozenset()
    symbolic_literals: frozenset[str] = frozenset()
    env_keys: frozenset[str] = frozenset()
    config_keys: frozenset[str] = frozenset()


_CONTRACT_LOOKUP_FIELDS = (
    "routes",
    "hooks",
    "registered_keys",
    "symbolic_literals",
    "env_keys",
    "config_keys",
)


def build_contract_inventory(store: IndexStore) -> dict[str, Any]:
    """Build a machine-readable inventory of declared runtime contracts."""
    occurrences = _collect_contract_occurrences(store)
    categories = {
        name: _serialize_category(entries)
        for name, entries in occurrences.items()
        if entries
    }

    return {
        "summary": {
            name: sum(item["count"] for item in items)
            for name, items in categories.items()
        },
        **categories,
    }


def build_contract_lookup(store: IndexStore) -> ContractLookup:
    """Build fast lookup sets for declared contract values."""
    occurrences = _collect_contract_occurrences(store)
    return ContractLookup(
        routes=frozenset(occurrences["routes"].keys()),
        hooks=frozenset(occurrences["hooks"].keys()),
        registered_keys=frozenset(occurrences["registered_keys"].keys()),
        symbolic_literals=frozenset(occurrences["symbolic_literals"].keys()),
        env_keys=frozenset(occurrences["env_keys"].keys()),
        config_keys=frozenset(occurrences["config_keys"].keys()),
    )


def merge_contract_lookup(
    base: ContractLookup,
    patch: ContractLookup | Mapping[str, Iterable[str]] | None,
) -> ContractLookup:
    """Merge plugin-provided contract values into an existing lookup."""
    if patch is None:
        return base

    patch_map: dict[str, Iterable[str]]
    if isinstance(patch, ContractLookup):
        patch_map = {field: getattr(patch, field) for field in _CONTRACT_LOOKUP_FIELDS}
    else:
        patch_map = {
            str(field): values
            for field, values in patch.items()
            if str(field) in _CONTRACT_LOOKUP_FIELDS
        }

    merged: dict[str, frozenset[str]] = {}
    for field in _CONTRACT_LOOKUP_FIELDS:
        base_values = set(getattr(base, field))
        patch_values = patch_map.get(field, ())
        base_values.update(
            str(value).strip() for value in patch_values if str(value).strip()
        )
        merged[field] = frozenset(base_values)

    return ContractLookup(**merged)


def _collect_contract_occurrences(
    store: IndexStore,
) -> dict[str, dict[str, dict[str, Any]]]:
    occurrences: dict[str, dict[str, dict[str, Any]]] = {
        "routes": defaultdict(lambda: {"count": 0, "locations": []}),
        "hooks": defaultdict(lambda: {"count": 0, "locations": []}),
        "registered_keys": defaultdict(lambda: {"count": 0, "locations": []}),
        "symbolic_literals": defaultdict(lambda: {"count": 0, "locations": []}),
        "env_keys": defaultdict(lambda: {"count": 0, "locations": []}),
        "config_keys": defaultdict(lambda: {"count": 0, "locations": []}),
    }

    for row in store.conn.execute("SELECT path, language FROM files ORDER BY path"):
        file_path = row["path"]
        language = str(row["language"]).lower()
        if language not in _SUPPORTED_LANGUAGES:
            continue
        if _is_test_like_path(file_path):
            continue
        content = _read_project_file(store, file_path)
        if content is None:
            continue

        for category, patterns in _PATTERN_MAP.items():
            _scan_patterns(occurrences[category], patterns, file_path, content)
        _scan_symbolic_literals(
            occurrences["symbolic_literals"], file_path, content, language
        )

    return occurrences


def _scan_patterns(
    bucket: dict[str, dict[str, Any]],
    patterns: tuple[re.Pattern[str], ...],
    file_path: str,
    content: str,
) -> None:
    for pattern in patterns:
        for match in pattern.finditer(content):
            value = match.group("value").strip()
            if not value:
                continue
            line = content[: match.start()].count("\n") + 1
            entry = bucket[value]
            entry["count"] += 1
            if len(entry["locations"]) < _MAX_SAMPLE_LOCATIONS:
                entry["locations"].append({"file": file_path, "line": line})


def _scan_symbolic_literals(
    bucket: dict[str, dict[str, Any]],
    file_path: str,
    content: str,
    language: str,
) -> None:
    if language in {"typescript", "javascript", "vue"}:
        _scan_multiline_type_unions(bucket, file_path, content)
        for pattern in _TS_LITERAL_UNION_PATTERNS:
            for match in pattern.finditer(content):
                _add_symbolic_values(
                    bucket, file_path, content, match.start(), match.group("body")
                )
        for match in _TS_CONST_ARRAY_PATTERN.finditer(content):
            _add_symbolic_values(
                bucket, file_path, content, match.start(), match.group("body")
            )
        _scan_stimulus_contracts(bucket, file_path, content)
        _scan_custom_events(bucket, file_path, content)
    if language == "php":
        for match in _PHP_REGISTER_ARRAY_PATTERN.finditer(content):
            body = match.group("body")
            for key_match in _ARRAY_KEY_PATTERN.finditer(body):
                value = key_match.group("value").strip()
                if not value:
                    continue
                _add_bucket_entry(
                    bucket,
                    value,
                    file_path,
                    content[: match.start() + key_match.start()].count("\n") + 1,
                )


def _scan_stimulus_contracts(
    bucket: dict[str, dict[str, Any]],
    file_path: str,
    content: str,
) -> None:
    controller_id = _stimulus_controller_identifier_for_path(file_path)
    if controller_id:
        _add_bucket_entry(bucket, controller_id, file_path, 1)

    for match in _DATA_CONTROLLER_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        for value in match.group("value").split():
            normalized = value.strip()
            if normalized:
                _add_bucket_entry(bucket, normalized, file_path, line)

    for match in _DATA_ACTION_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        for controller_match in _ACTION_CONTROLLER_PATTERN.finditer(
            match.group("value")
        ):
            normalized = controller_match.group("value").strip()
            if normalized:
                _add_bucket_entry(bucket, normalized, file_path, line)


def _scan_custom_events(
    bucket: dict[str, dict[str, Any]],
    file_path: str,
    content: str,
) -> None:
    for match in _CUSTOM_EVENT_PATTERN.finditer(content):
        _add_bucket_entry(
            bucket,
            match.group("value").strip(),
            file_path,
            content[: match.start()].count("\n") + 1,
        )


def _add_symbolic_values(
    bucket: dict[str, dict[str, Any]],
    file_path: str,
    content: str,
    start_index: int,
    body: str,
) -> None:
    for value_match in _QUOTED_VALUE_PATTERN.finditer(body):
        value = value_match.group("value").strip()
        if not value:
            continue
        line = content[: start_index + value_match.start()].count("\n") + 1
        entry = bucket[value]
        entry["count"] += 1
        if len(entry["locations"]) < _MAX_SAMPLE_LOCATIONS:
            entry["locations"].append({"file": file_path, "line": line})


def _add_bucket_entry(
    bucket: dict[str, dict[str, Any]],
    value: str,
    file_path: str,
    line: int,
) -> None:
    entry = bucket[value]
    entry["count"] += 1
    if len(entry["locations"]) < _MAX_SAMPLE_LOCATIONS:
        entry["locations"].append({"file": file_path, "line": line})


def _scan_multiline_type_unions(
    bucket: dict[str, dict[str, Any]],
    file_path: str,
    content: str,
) -> None:
    lines = content.splitlines()
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    type_start = re.compile(r"\b(?:export\s+)?type\s+[A-Za-z0-9_<>,\s]+\s*=\s*$")
    i = 0
    while i < len(lines):
        if not type_start.search(lines[i]):
            i += 1
            continue
        body_lines: list[str] = []
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            body_lines.append(lines[j])
            j += 1
        if body_lines:
            _add_symbolic_values(
                bucket,
                file_path,
                content,
                offsets[i],
                "\n".join(body_lines),
            )
        i = max(j, i + 1)


def _serialize_category(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            "value": value,
            "count": data["count"],
            "locations": data["locations"],
        }
        for value, data in entries.items()
    ]
    items.sort(key=lambda item: (-item["count"], item["value"]))
    return items[:_MAX_ITEMS_PER_CATEGORY]


def _read_project_file(store: IndexStore, relative_path: str) -> str | None:
    db_path = Path(store.conn.execute("PRAGMA database_list").fetchone()["file"])
    project_root = db_path.parent.parent
    full_path = project_root / relative_path
    if not full_path.exists():
        return None
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _is_test_like_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    stem = Path(normalized).stem.lower()
    parts = [part for part in normalized.split("/") if part]
    return (
        "test" in stem
        or any(
            part in {"test", "tests", "__tests__", "fixtures", "spec"} for part in parts
        )
        or normalized.endswith("test.php")
        or normalized.endswith("_test.py")
        or normalized.endswith("_test.rb")
        or normalized.endswith("_spec.rb")
    )


def _stimulus_controller_identifier_for_path(file_path: str) -> str | None:
    normalized = file_path.replace("\\", "/")
    lower = normalized.lower()
    marker = "/controllers/"
    if marker not in lower:
        return None

    relative = normalized[lower.index(marker) + len(marker) :]
    pure_relative = PurePosixPath(relative)
    suffixes = "".join(pure_relative.suffixes)
    if suffixes not in {".js", ".ts", ".jsx", ".tsx", ".vue"}:
        return None

    relative_no_ext = relative[: -len(suffixes)] if suffixes else relative
    if not relative_no_ext.endswith("_controller"):
        return None

    controller_path = relative_no_ext[: -len("_controller")]
    parts = [part.replace("_", "-") for part in controller_path.split("/") if part]
    if not parts:
        return None
    return "--".join(parts)
