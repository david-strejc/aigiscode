"""Dead code analysis using indexed symbol and dependency data.

Detects unused imports, unused private methods, unused private properties,
and abandoned classes by cross-referencing the symbol index against actual
file contents.
"""

from __future__ import annotations

import ast
import logging
import re
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

from aigiscode.indexer.store import IndexStore
from aigiscode.policy.models import DeadCodePolicy

logger = logging.getLogger(__name__)
SUPPORTED_DEAD_CODE_LANGUAGES = frozenset(
    {"php", "python", "javascript", "typescript", "vue", "rust"}
)
_TS_LIKE_LANGUAGES = frozenset({"javascript", "typescript", "vue"})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DeadCodeFinding:
    """A single dead code finding."""

    file_path: str
    line: int
    category: (
        str  # 'unused_import', 'unused_method', 'unused_property', 'abandoned_class'
    )
    name: str
    detail: str
    confidence: str = "high"  # 'high', 'medium', 'low'


@dataclass
class DeadCodeResult:
    """Full dead code analysis result."""

    unused_imports: list[DeadCodeFinding] = field(default_factory=list)
    unused_methods: list[DeadCodeFinding] = field(default_factory=list)
    unused_properties: list[DeadCodeFinding] = field(default_factory=list)
    abandoned_classes: list[DeadCodeFinding] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.unused_imports)
            + len(self.unused_methods)
            + len(self.unused_properties)
            + len(self.abandoned_classes)
        )


@dataclass(frozen=True)
class JSImportBinding:
    """A bound import name inside a JS/TS/Vue file."""

    name: str
    source: str
    line: int
    type_only: bool = False


@dataclass(frozen=True)
class RustImportBinding:
    """A bound import name inside a Rust file."""

    name: str
    target: str
    line: int


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def analyze_dead_code(
    store: IndexStore, policy: DeadCodePolicy | None = None
) -> DeadCodeResult:
    """Run all dead code analyses using the indexed data.

    Each sub-analysis is timed independently so performance characteristics
    are visible in the logs.
    """
    t0 = time.monotonic()
    result = DeadCodeResult()

    if policy is None:
        policy = DeadCodePolicy()

    result.unused_imports = find_unused_imports(
        store, attribute_names=policy.attribute_usage_names
    )
    logger.info(
        "Unused imports: %d found in %.2fs",
        len(result.unused_imports),
        time.monotonic() - t0,
    )

    t1 = time.monotonic()
    result.unused_methods = find_unused_private_methods(store)
    logger.info(
        "Unused methods: %d found in %.2fs",
        len(result.unused_methods),
        time.monotonic() - t1,
    )

    t1 = time.monotonic()
    result.unused_properties = find_unused_private_properties(store)
    logger.info(
        "Unused properties: %d found in %.2fs",
        len(result.unused_properties),
        time.monotonic() - t1,
    )

    t1 = time.monotonic()
    result.abandoned_classes = find_abandoned_classes(
        store,
        allowed_languages=policy.abandoned_languages,
        extra_entry_patterns=policy.abandoned_entry_patterns,
        dynamic_reference_patterns=policy.abandoned_dynamic_reference_patterns,
    )
    logger.info(
        "Abandoned classes: %d found in %.2fs",
        len(result.abandoned_classes),
        time.monotonic() - t1,
    )

    logger.info(
        "Dead code analysis complete: %d findings in %.2fs",
        result.total,
        time.monotonic() - t0,
    )
    return result


# ---------------------------------------------------------------------------
# 1. Unused imports
# ---------------------------------------------------------------------------


def find_unused_imports(
    store: IndexStore,
    attribute_names: list[str] | None = None,
) -> list[DeadCodeFinding]:
    """Find imports whose bound name never appears in the file body.

    PHP uses dependency rows plus body scanning.
    Python uses an AST pass over the current file content so aliases and
    ``__all__`` re-exports are handled from source rather than stale index rows.
    """
    findings = _find_unused_php_imports(
        store,
        attribute_names=attribute_names,
    )
    findings.extend(_find_unused_python_imports(store))
    findings.extend(_find_unused_ts_like_imports(store))
    findings.extend(_find_unused_rust_imports(store))
    return findings


def _find_unused_php_imports(
    store: IndexStore,
    attribute_names: list[str] | None = None,
) -> list[DeadCodeFinding]:
    """Find PHP ``use`` imports whose imported name never appears in the file body."""
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT f.id, f.path, d.target_name, d.line
        FROM dependencies d
        JOIN files f ON f.id = d.source_file_id
        WHERE d.type = 'import' AND f.language = 'php'
        ORDER BY f.id, d.line
        """
    ).fetchall()

    # Group imports by file id
    imports_by_file: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for row in rows:
        imports_by_file[row["id"]].append(
            (row["path"], row["target_name"], row["line"])
        )

    names_used_in_attributes = set(attribute_names or [])

    for file_id, imports in imports_by_file.items():
        if not imports:
            continue

        file_path = imports[0][0]

        content = _read_file_safe(store, file_path)
        if content is None:
            continue

        body = _extract_body(content)

        # Parse aliases from use statements: "use Foo\Bar as Baz;" -> alias = "Baz"
        alias_map = _parse_import_aliases(content)

        for _, target_name, line_no in imports:
            short_name = target_name.rsplit("\\", 1)[-1]

            # Skip names that are commonly used implicitly (attributes, annotations)
            if short_name in names_used_in_attributes:
                continue

            # If the import has an alias, search for the alias instead
            search_name = alias_map.get(target_name, short_name)
            import_line = _line_at(content, line_no)
            if not _line_contains_php_import(import_line, search_name):
                continue

            pattern = r"\b" + re.escape(search_name) + r"\b"
            if _is_used_in_php_attributes(content, search_name):
                continue
            if _is_used_in_php_docblocks(content, search_name):
                continue
            if not re.search(pattern, body):
                findings.append(
                    DeadCodeFinding(
                        file_path=file_path,
                        line=line_no,
                        category="unused_import",
                        name=target_name,
                        detail=(
                            f"Import '{target_name}' (as {search_name}) "
                            f"not found in file body"
                        ),
                        confidence="high",
                    )
                )

    return findings


def _find_unused_python_imports(store: IndexStore) -> list[DeadCodeFinding]:
    """Find Python imports whose bound names are never referenced."""
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT path
        FROM files
        WHERE language = 'python'
        ORDER BY path
        """
    ).fetchall()

    for row in rows:
        file_path = row["path"]
        content = _read_file_safe(store, file_path)
        if content is None:
            continue
        findings.extend(_analyze_python_unused_imports(file_path, content))

    return findings


def _find_unused_rust_imports(store: IndexStore) -> list[DeadCodeFinding]:
    """Find Rust ``use`` bindings that are never referenced."""
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT path
        FROM files
        WHERE language = 'rust'
        ORDER BY path
        """
    ).fetchall()

    parser = _get_tree_sitter_parser("rust")
    if parser is None:
        return findings

    for row in rows:
        file_path = row["path"]
        if _is_test_like_path(file_path):
            continue

        content = _read_file_safe(store, file_path)
        if content is None or not content.strip():
            continue

        try:
            tree = parser.parse(content.encode("utf-8"))
        except Exception:
            continue

        bindings = _collect_rust_import_bindings(tree.root_node)
        if not bindings:
            continue
        used_names = _collect_rust_used_identifiers(tree.root_node)

        for binding in bindings:
            if binding.name in used_names:
                continue
            findings.append(
                DeadCodeFinding(
                    file_path=file_path,
                    line=binding.line,
                    category="unused_import",
                    name=binding.target,
                    detail=(
                        f"Import '{binding.name}' from '{binding.target}' "
                        "is never referenced"
                    ),
                    confidence="high",
                )
            )

    return findings


def _find_unused_ts_like_imports(store: IndexStore) -> list[DeadCodeFinding]:
    """Find unused imports in JS/TS/Vue source files."""
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT path, lower(language) AS language
        FROM files
        WHERE lower(language) IN ('javascript', 'typescript', 'vue')
        ORDER BY path
        """
    ).fetchall()

    for row in rows:
        file_path = row["path"]
        language = row["language"]
        if _is_test_like_path(file_path) or file_path.endswith(".d.ts"):
            continue

        content = _read_file_safe(store, file_path)
        if content is None:
            continue

        if language == "vue":
            template_content = _extract_vue_template_surface(content)
            script_blocks = _extract_vue_inline_script_blocks(content)
            for script_content, line_offset in script_blocks:
                findings.extend(
                    _analyze_ts_like_unused_imports(
                        file_path,
                        script_content,
                        parser_language="typescript",
                        line_offset=line_offset,
                        vue_template_content=template_content,
                        confidence="medium",
                    )
                )
            continue

        parser_language = _ts_like_parser_language(file_path, language)
        findings.extend(
            _analyze_ts_like_unused_imports(
                file_path,
                content,
                parser_language=parser_language,
                line_offset=0,
                vue_template_content=None,
                confidence="high",
            )
        )

    return findings


def _ts_like_parser_language(file_path: str, language: str) -> str:
    if language == "typescript" and file_path.endswith(".tsx"):
        return "tsx"
    return "typescript" if language == "typescript" else "javascript"


def _analyze_ts_like_unused_imports(
    file_path: str,
    content: str,
    *,
    parser_language: str,
    line_offset: int,
    vue_template_content: str | None,
    confidence: str,
) -> list[DeadCodeFinding]:
    if not content.strip():
        return []

    parser = _get_tree_sitter_parser(parser_language)
    if parser is None:
        return []

    try:
        tree = parser.parse(content.encode("utf-8"))
    except Exception:
        return []

    bindings = _collect_ts_import_bindings(tree.root_node)
    if not bindings:
        return []

    used_names = _collect_ts_used_identifiers(tree.root_node)
    findings: list[DeadCodeFinding] = []

    for binding in bindings:
        if binding.name in used_names:
            continue
        if vue_template_content and _vue_template_uses_binding(
            vue_template_content, binding.name
        ):
            continue
        findings.append(
            DeadCodeFinding(
                file_path=file_path,
                line=binding.line + line_offset,
                category="unused_import",
                name=binding.name,
                detail=(
                    f"Import '{binding.name}' from '{binding.source}' "
                    "is never referenced"
                ),
                confidence=confidence,
            )
        )

    return findings


# ---------------------------------------------------------------------------
# 2. Unused private methods
# ---------------------------------------------------------------------------


def find_unused_private_methods(store: IndexStore) -> list[DeadCodeFinding]:
    """Find private methods that are never called within the same file.

    For each private method we search the file content for call-site patterns
    (``methodName(``) on lines other than the declaration.
    Magic methods (``__*``) are excluded because they are invoked by the
    runtime, not by explicit user code.
    """
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT s.name, s.line_start, s.file_id, f.path, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type = 'method' AND s.visibility = 'private'
        ORDER BY f.id
        """
    ).fetchall()

    methods_by_file: dict[int, list] = defaultdict(list)
    for row in rows:
        methods_by_file[row["file_id"]].append(row)

    for file_id, methods in methods_by_file.items():
        if not methods:
            continue

        file_path = methods[0]["path"]
        content = _read_file_safe(store, file_path)
        if content is None:
            continue

        for method in methods:
            name = method["name"].lstrip("#")
            language = str(method["language"]).lower()

            # Magic methods are invoked by the runtime
            if name.startswith("__"):
                continue

            declaration_line = _line_at(content, method["line_start"])
            if not _line_contains_method_declaration(declaration_line, name):
                continue
            is_property_style_accessor = bool(
                declaration_line
                and re.search(rf"\b(get|set)\s+{re.escape(name)}\b", declaration_line)
            )

            patterns = [r"\b" + re.escape(name) + r"\s*\("]
            if language in _TS_LIKE_LANGUAGES:
                patterns.extend(
                    [
                        r"\bthis\." + re.escape(name) + r"\b",
                        r"\bthis\.#" + re.escape(name) + r"\b",
                    ]
                )
            elif language == "rust":
                patterns.extend(
                    [
                        r"\.\s*" + re.escape(name) + r"\s*\(",
                        r"::" + re.escape(name) + r"\s*\(",
                    ]
                )
            if is_property_style_accessor:
                patterns.extend(
                    [
                        r"\bthis\." + re.escape(name) + r"\b",
                        r"->" + re.escape(name) + r"\b",
                    ]
                )

            matches = []
            for pattern in patterns:
                matches.extend(re.finditer(pattern, content))
            matches.extend(_find_private_method_callback_matches(content, name))

            non_declaration_matches = 0
            for m in matches:
                line_no = content[: m.start()].count("\n") + 1
                if line_no != method["line_start"]:
                    non_declaration_matches += 1

            if non_declaration_matches == 0:
                findings.append(
                    DeadCodeFinding(
                        file_path=file_path,
                        line=method["line_start"],
                        category="unused_method",
                        name=name,
                        detail=f"Private method '{name}()' has no calls in the same file",
                        confidence="high",
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# 3. Unused private properties
# ---------------------------------------------------------------------------


def find_unused_private_properties(store: IndexStore) -> list[DeadCodeFinding]:
    """Find private properties that are never referenced beyond their declaration.

    We search for both ``->propertyName`` (object access) and ``$propertyName``
    (variable reference) patterns.  Only references on lines *other than* the
    declaration are counted.

    Confidence is ``medium`` because PHP magic methods (``__get`` / ``__set``)
    can access properties dynamically.
    """
    findings: list[DeadCodeFinding] = []

    rows = store.conn.execute(
        """
        SELECT s.name, s.line_start, s.file_id, f.path, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type = 'property' AND s.visibility = 'private'
        ORDER BY f.id
        """
    ).fetchall()

    props_by_file: dict[int, list] = defaultdict(list)
    for row in rows:
        props_by_file[row["file_id"]].append(row)

    for file_id, props in props_by_file.items():
        if not props:
            continue

        file_path = props[0]["path"]
        content = _read_file_safe(store, file_path)
        if content is None:
            continue

        for prop in props:
            name = prop["name"].lstrip("#")
            language = prop["language"]
            declaration_line = _line_at(content, prop["line_start"])
            if not _line_contains_property_declaration(
                declaration_line, name, language
            ):
                continue

            if language in {"typescript", "javascript", "vue"}:
                patterns = [
                    r"\bthis\." + re.escape(name) + r"\b",
                    r"\bthis\.#" + re.escape(name) + r"\b",
                ]
            elif language == "rust":
                patterns = [
                    r"\." + re.escape(name) + r"\b(?!\s*\()",
                    r"\b" + re.escape(name) + r"\s*:",
                ]
            else:
                patterns = [
                    r"->" + re.escape(name) + r"\b",
                    r"\$" + re.escape(name) + r"\b",
                ]

            total_refs = 0
            for pat in patterns:
                for m in re.finditer(pat, content):
                    line_no = content[: m.start()].count("\n") + 1
                    if line_no != prop["line_start"]:
                        total_refs += 1

            if total_refs == 0:
                findings.append(
                    DeadCodeFinding(
                        file_path=file_path,
                        line=prop["line_start"],
                        category="unused_property",
                        name=name,
                        detail=f"Private property '${name}' not referenced beyond declaration",
                        confidence="medium",
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# 4. Abandoned classes
# ---------------------------------------------------------------------------

# Files matching these path fragments are framework entry points that are
# auto-discovered at runtime and therefore expected to have zero external
# references.
_DEFAULT_ENTRY_POINT_PATTERNS = [
    "/Commands/",
    "/Console/",
    "/Controllers/",
    "/controllers/",
    "/Hooks/",
    "/hooks/",
    "/Middleware/",
    "/Providers/",
    "/bootstrap/",
    "/migrations/",
    "/seeders/",
    "/factories/",
    "/tests/",
    "/Tests/",
    "/Actions/",
    "/Policies/",
    "/Observers/",
    "/Listeners/",
    "/Events/",
    "/Jobs/",
    "/Mail/",
    "/Notifications/",
    "/Rules/",
    "/Casts/",
    "/Exceptions/",
    # Entity classes are auto-discovered by the framework via directory
    # scanning and #[EntityAttr] attributes — not explicitly imported.
    "/Entities/",
    "/Models/",
]


def find_abandoned_classes(
    store: IndexStore,
    allowed_languages: list[str] | None = None,
    extra_entry_patterns: list[str] | None = None,
    dynamic_reference_patterns: list[str] | None = None,
) -> list[DeadCodeFinding]:
    """Find classes that are never referenced by any other file.

    A class is considered "abandoned" when its fully-qualified class name
    *and* its short name do not appear in any ``import``, ``inherit``, or
    ``implement`` dependency from another file.

    Framework entry-point directories and test files are excluded because
    they are auto-discovered by the framework rather than explicitly imported.
    """
    findings: list[DeadCodeFinding] = []
    allowed_language_set = {
        value.strip().lower()
        for value in (allowed_languages or ["php"])
        if value and value.strip()
    }
    if not allowed_language_set:
        return []
    language_placeholders = ", ".join("?" for _ in allowed_language_set)

    # All class-level symbols
    classes = store.conn.execute(
        f"""
        SELECT s.id, s.name, s.namespace, s.type, s.visibility, s.line_start,
               f.path, f.id AS file_id, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type IN ('class', 'interface', 'trait', 'enum')
          AND lower(f.language) IN ({language_placeholders})
        """,
        tuple(sorted(allowed_language_set)),
    ).fetchall()
    known_class_tokens = {
        token
        for cls in classes
        for token in (
            cls["name"],
            f"{cls['namespace']}\\{cls['name']}" if cls["namespace"] else cls["name"],
        )
        if token
    }

    # Build the set of all names that appear as dependency targets
    referenced: set[str] = set()
    dep_rows = store.conn.execute(
        """
        SELECT DISTINCT target_name
        FROM dependencies
        WHERE type IN ('import', 'inherit', 'implement', 'register')
        """
    ).fetchall()

    for row in dep_rows:
        target = row["target_name"]
        referenced.add(target)
        # Also index the short name so that FQN-vs-short mismatches are
        # resolved in favour of "used".
        short = target.rsplit("\\", 1)[-1]
        referenced.add(short)

    referenced.update(
        _collect_runtime_class_references(
            store,
            allowed_languages=allowed_language_set,
        )
    )
    referenced.update(
        _collect_runtime_string_class_references(
            store,
            allowed_languages=allowed_language_set,
            known_class_tokens=known_class_tokens,
        )
    )
    rust_type_references = _collect_rust_type_reference_map(store)

    entry_patterns = [*_DEFAULT_ENTRY_POINT_PATTERNS, *(extra_entry_patterns or [])]
    dynamic_patterns = [
        "**/*.hook.php",
        "**/*.hooks.php",
        "**/module.php",
        "**/Module.php",
        "**/manifest.php",
        "**/Manifest.php",
        "**/bootstrap/**/*.php",
        "**/routes/**/*.php",
        "**/*ServiceProvider.php",
        *(dynamic_reference_patterns or []),
    ]
    dynamic_reference_tokens = _collect_dynamic_reference_tokens(
        store, dynamic_patterns
    )
    file_cache: dict[str, str | None] = {}

    for cls in classes:
        language = str(cls["language"]).lower()
        if language not in allowed_language_set:
            continue
        if language == "rust" and str(cls["visibility"]).lower() != "public":
            continue

        fqcn = f"{cls['namespace']}\\{cls['name']}" if cls["namespace"] else cls["name"]
        short_name = cls["name"]

        # Already referenced -- skip
        if fqcn in referenced or short_name in referenced:
            continue
        if fqcn in dynamic_reference_tokens or short_name in dynamic_reference_tokens:
            continue

        path = cls["path"]
        if path not in file_cache:
            file_cache[path] = _read_file_safe(store, path)
        content = file_cache[path]
        if content is None:
            continue
        if not _line_contains_type_declaration(
            _line_at(content, cls["line_start"]),
            short_name,
            cls["type"],
        ):
            continue

        # Skip framework entry points
        normalized_path = path.replace("\\", "/")
        if any(_path_matches_pattern(normalized_path, pat) for pat in entry_patterns):
            continue

        # Skip test files
        if _is_test_like_path(path):
            continue

        # Check for cross-file references by short name suffix match
        self_deps = store.conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM dependencies
            WHERE target_name LIKE ? AND source_file_id != ?
            """,
            (f"%{short_name}", cls["file_id"]),
        ).fetchone()

        if self_deps and self_deps["cnt"] > 0:
            continue
        if language == "rust":
            referenced_paths = rust_type_references.get(short_name, set())
            if any(path != cls["path"] for path in referenced_paths):
                continue

        findings.append(
            DeadCodeFinding(
                file_path=path,
                line=cls["line_start"],
                category="abandoned_class",
                name=fqcn,
                detail=(
                    f"{cls['type'].capitalize()} '{short_name}' "
                    f"has no external references"
                ),
                confidence="medium",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_import_aliases(content: str) -> dict[str, str]:
    """Extract ``use Foo\\Bar as Alias;`` mappings from PHP source.

    Returns a dict mapping the fully-qualified name to its alias.
    Only aliased imports are included; non-aliased imports are omitted.
    """
    aliases: dict[str, str] = {}
    for m in re.finditer(
        r"^\s*use\s+([\w\\]+)\s+as\s+(\w+)\s*;",
        content,
        re.MULTILINE,
    ):
        fqcn = m.group(1)
        alias = m.group(2)
        aliases[fqcn] = alias
    return aliases


def _analyze_python_unused_imports(
    file_path: str,
    content: str,
) -> list[DeadCodeFinding]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    used_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    exported_names = _extract_python_all_exports(tree)
    findings: list[DeadCodeFinding] = []
    is_package_init = Path(file_path).name == "__init__.py"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "__future__":
                    continue
                binding_name = alias.asname or alias.name.split(".", 1)[0]
                if binding_name in used_names:
                    continue
                findings.append(
                    DeadCodeFinding(
                        file_path=file_path,
                        line=node.lineno,
                        category="unused_import",
                        name=alias.name,
                        detail=(
                            f"Import '{alias.name}' binds '{binding_name}', "
                            "which is never referenced"
                        ),
                        confidence="low" if is_package_init else "high",
                    )
                )
            continue

        if not isinstance(node, ast.ImportFrom):
            continue

        if node.module == "__future__":
            continue

        for alias in node.names:
            if alias.name == "*":
                continue
            binding_name = alias.asname or alias.name
            if binding_name in used_names or binding_name in exported_names:
                continue
            target_name = (
                ".".join(part for part in (node.module, alias.name) if part)
                if node.module
                else alias.name
            )
            findings.append(
                DeadCodeFinding(
                    file_path=file_path,
                    line=node.lineno,
                    category="unused_import",
                    name=target_name,
                    detail=(
                        f"Import '{binding_name}' from '{node.module or '.'}' "
                        "is never referenced"
                    ),
                    confidence="low" if is_package_init else "high",
                )
            )

    return findings


@lru_cache(maxsize=2)
def _get_tree_sitter_parser(language: str):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            from tree_sitter_languages import get_parser

            return get_parser(language)
    except Exception:
        return None


def _node_text(node) -> str:
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _find_tree_child(node, type_name: str):
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_tree_children(node, type_name: str) -> list:
    return [child for child in node.children if child.type == type_name]


def _collect_rust_import_bindings(root_node) -> list[RustImportBinding]:
    bindings: list[RustImportBinding] = []
    seen: set[tuple[str, str, int]] = set()

    def walk(node) -> None:
        if node.type == "use_declaration":
            statement = _node_text(node).strip()
            if statement.startswith("use "):
                for binding in _expand_rust_use_bindings(
                    statement[4:].rstrip(";").strip(),
                    line=node.start_point[0] + 1,
                ):
                    key = (binding.name, binding.target, binding.line)
                    if key in seen:
                        continue
                    seen.add(key)
                    bindings.append(binding)
            return

        for child in node.children:
            walk(child)

    walk(root_node)
    return bindings


def _collect_rust_used_identifiers(root_node) -> set[str]:
    used: set[str] = set()

    def walk(node, in_use: bool = False) -> None:
        current_in_use = in_use or node.type == "use_declaration"
        if not current_in_use and node.type in {"identifier", "type_identifier"}:
            text = _node_text(node)
            if text:
                used.add(text)
        for child in node.children:
            walk(child, current_in_use)

    walk(root_node)
    return used


def _expand_rust_use_bindings(path: str, *, line: int) -> list[RustImportBinding]:
    path = path.strip()
    if not path:
        return []
    alias_match = re.search(r"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)$", path)
    if alias_match:
        target = path[: alias_match.start()].strip()
        return [
            RustImportBinding(
                name=alias_match.group(1),
                target=target.replace(" ", ""),
                line=line,
            )
        ]
    if "{" not in path:
        normalized = path.replace(" ", "")
        binding = normalized.rsplit("::", 1)[-1]
        return [RustImportBinding(name=binding, target=normalized, line=line)]

    brace_start = path.find("{")
    brace_end = path.rfind("}")
    if brace_start == -1 or brace_end == -1 or brace_end < brace_start:
        normalized = path.replace(" ", "")
        binding = normalized.rsplit("::", 1)[-1]
        return [RustImportBinding(name=binding, target=normalized, line=line)]

    prefix = path[:brace_start].rstrip(":").strip()
    inner = path[brace_start + 1 : brace_end]
    bindings: list[RustImportBinding] = []
    for item in _split_rust_use_items(inner):
        item = item.strip()
        if not item or item == "*":
            continue
        if item == "self":
            normalized = prefix.replace(" ", "")
            bindings.append(
                RustImportBinding(
                    name=normalized.rsplit("::", 1)[-1],
                    target=normalized,
                    line=line,
                )
            )
            continue
        candidate = item
        if prefix and not item.startswith(("crate::", "self::", "super::")):
            candidate = f"{prefix}::{item}"
        bindings.extend(_expand_rust_use_bindings(candidate, line=line))
    return bindings


def _split_rust_use_items(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        current.append(char)
    if current:
        items.append("".join(current).strip())
    return items


def _collect_ts_import_bindings(root_node) -> list[JSImportBinding]:
    bindings: list[JSImportBinding] = []
    seen: set[tuple[str, str, int]] = set()

    def walk(node) -> None:
        if node.type == "import_statement":
            source_node = _find_tree_child(node, "string")
            import_clause = _find_tree_child(node, "import_clause")
            if source_node is None or import_clause is None:
                return

            source = _node_text(source_node).strip("'\"")
            statement_type_only = any(child.type == "type" for child in node.children)
            line = node.start_point[0] + 1

            for binding in _extract_ts_import_clause_bindings(
                import_clause,
                source=source,
                line=line,
                statement_type_only=statement_type_only,
            ):
                key = (binding.name, binding.source, binding.line)
                if key in seen:
                    continue
                seen.add(key)
                bindings.append(binding)
            return

        for child in node.children:
            walk(child)

    walk(root_node)
    return bindings


def _extract_ts_import_clause_bindings(
    import_clause,
    *,
    source: str,
    line: int,
    statement_type_only: bool,
) -> list[JSImportBinding]:
    bindings: list[JSImportBinding] = []

    for child in import_clause.children:
        if child.type == "identifier":
            bindings.append(
                JSImportBinding(
                    name=_node_text(child),
                    source=source,
                    line=line,
                    type_only=statement_type_only,
                )
            )
            continue

        if child.type == "namespace_import":
            identifier = _find_tree_child(child, "identifier")
            if identifier is None:
                continue
            bindings.append(
                JSImportBinding(
                    name=_node_text(identifier),
                    source=source,
                    line=line,
                    type_only=statement_type_only,
                )
            )
            continue

        if child.type != "named_imports":
            continue

        for specifier in _find_tree_children(child, "import_specifier"):
            identifiers = [
                _node_text(grandchild)
                for grandchild in specifier.children
                if grandchild.type == "identifier"
            ]
            if not identifiers:
                continue
            bindings.append(
                JSImportBinding(
                    name=identifiers[-1],
                    source=source,
                    line=line,
                    type_only=statement_type_only
                    or any(
                        grandchild.type == "type" for grandchild in specifier.children
                    ),
                )
            )

    return bindings


def _collect_ts_used_identifiers(root_node) -> set[str]:
    used_names: set[str] = set()

    def walk(node) -> None:
        if node.type == "import_statement":
            return

        if node.type in {
            "identifier",
            "type_identifier",
            "shorthand_property_identifier",
            "shorthand_property_identifier_pattern",
        }:
            value = _node_text(node).strip()
            if value:
                used_names.add(value)

        for child in node.children:
            walk(child)

    walk(root_node)
    return used_names


def _extract_vue_inline_script_blocks(content: str) -> list[tuple[str, int]]:
    blocks: list[tuple[str, int]] = []
    for match in re.finditer(
        r"(?is)<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        content,
    ):
        attrs = match.group("attrs") or ""
        if re.search(r"\bsrc\s*=", attrs):
            continue
        body = match.group("body")
        if not body.strip():
            continue
        line_offset = content[: match.start("body")].count("\n")
        blocks.append((body, line_offset))
    return blocks


def _extract_vue_template_surface(content: str) -> str:
    without_scripts = re.sub(
        r"(?is)<script\b[^>]*>.*?</script>",
        "\n",
        content,
    )
    return re.sub(
        r"(?is)<style\b[^>]*>.*?</style>",
        "\n",
        without_scripts,
    )


def _vue_template_uses_binding(template_content: str, name: str) -> bool:
    if not template_content or not name:
        return False
    if re.search(rf"\b{re.escape(name)}\b", template_content):
        return True

    kebab_name = _to_kebab_case(name)
    if kebab_name != name and re.search(
        rf"<\s*/?\s*{re.escape(kebab_name)}\b", template_content
    ):
        return True

    return False


def _to_kebab_case(name: str) -> str:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", normalized)
    return normalized.replace("_", "-").lower()


def _extract_python_all_exports(tree: ast.Module) -> set[str]:
    """Collect names explicitly re-exported via ``__all__``."""
    exported_names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            if "__all__" not in targets:
                continue
            exported_names.update(_extract_python_string_sequence(node.value))
            continue

        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                exported_names.update(_extract_python_string_sequence(node.value))

    return exported_names


def _extract_python_string_sequence(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set()
    return {
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _read_file_safe(store: IndexStore, relative_path: str) -> str | None:
    """Read a source file from disk, resolving the path via the store's DB location.

    The database lives at ``<project_root>/.aigiscode/aigiscode.db``, so we
    derive the project root by going two levels up from the DB file.  File
    paths stored in the index are relative to that root.
    """
    db_path = Path(store.conn.execute("PRAGMA database_list").fetchone()["file"])
    project_root = db_path.parent.parent  # .aigiscode/ -> project root

    full_path = project_root / relative_path
    if not full_path.exists():
        return None

    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _extract_body(content: str) -> str:
    """Return the portion of a PHP file after the import/header preamble."""
    lines = content.split("\n")
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if (
            stripped.startswith("<?")
            or stripped.startswith("declare(")
            or stripped.startswith("namespace ")
            or stripped.startswith("use ")
            or stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.startswith("#[")
        ):
            body_start = i + 1
            continue
        break

    return "\n".join(lines[body_start:])


def _is_used_in_php_attributes(content: str, name: str) -> bool:
    """Check if imported symbol appears in PHP attribute syntax."""
    pattern = r"#\[\s*" + re.escape(name) + r"[\s:(]"
    return bool(re.search(pattern, content))


def _is_used_in_php_docblocks(content: str, name: str) -> bool:
    """Check if imported symbol appears in a PHPDoc type context."""
    pattern = re.compile(
        rf"@(?:param|return|var|property(?:-read|-write)?|method|throws|template|extends|implements)\b"
        rf"[^\n]*\b{re.escape(name)}\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(content))


def _line_at(content: str, line_no: int) -> str:
    """Return a single 1-based line from source text."""
    if line_no <= 0:
        return ""
    lines = content.splitlines()
    idx = line_no - 1
    if idx >= len(lines):
        return ""
    return lines[idx]


def _path_matches_pattern(path: str, pattern: str) -> bool:
    """Support both legacy substring patterns and glob patterns."""
    if not pattern:
        return False
    if any(ch in pattern for ch in ["*", "?", "["]):
        return fnmatch(path, pattern)
    return pattern in f"/{path}"


def _is_test_like_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    stem = Path(normalized).stem.lower()
    parts = [part for part in normalized.split("/") if part]
    return (
        "test" in stem
        or any(part in {"test", "tests", "__tests__", "fixtures"} for part in parts)
        or normalized.endswith("test.php")
        or normalized.endswith("_test.py")
        or normalized.endswith("_test.rs")
    )


def _line_contains_php_import(line: str, search_name: str) -> bool:
    """Verify the indexed import still exists on the reported line."""
    if not line:
        return False
    if not re.search(r"^\s*use\s+[\w\\]+(?:\s+as\s+\w+)?\s*;", line):
        return False
    return bool(re.search(rf"\b{re.escape(search_name)}\b", line))


def _line_contains_method_declaration(line: str, name: str) -> bool:
    """Guard against stale symbol rows pointing at non-declaration lines."""
    if not line:
        return False
    patterns = (
        rf"\bfunction\s+{re.escape(name)}\s*\(",
        rf"\b(?:public|protected|private|static|async|abstract|final|readonly|get|set)\b[^\n{{;]*\b{re.escape(name)}\s*\(",
        rf"\b(?:pub(?:\([^)]*\))?\s+)?fn\s+{re.escape(name)}\s*\(",
        rf"#{re.escape(name)}\s*\(",
    )
    return any(re.search(pattern, line) for pattern in patterns)


def _find_private_method_callback_matches(
    content: str, name: str
) -> list[re.Match[str]]:
    patterns = (
        rf"(?:array\s*\(|\[)\s*(?:\$this|self::class|static::class|__CLASS__)\s*,\s*['\"]{re.escape(name)}['\"]",
    )
    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, content))
    return matches


def _line_contains_property_declaration(line: str, name: str, language: str) -> bool:
    """Guard against stale property rows pointing at unrelated lines."""
    if not line:
        return False
    if language in {"typescript", "javascript", "vue"}:
        patterns = (
            rf"\b(?:public|protected|private|static|readonly)\b[^\n=;]*\b{re.escape(name)}\b",
            rf"#{re.escape(name)}\b",
        )
        return any(re.search(pattern, line) for pattern in patterns)
    if language == "rust":
        return bool(
            re.search(
                rf"\b(?:pub(?:\([^)]*\))?\s+)?{re.escape(name)}\s*:",
                line,
            )
        )
    return bool(
        re.search(
            rf"\b(?:public|protected|private|var|static|readonly)\b[^\n;]*\${re.escape(name)}\b",
            line,
        )
    )


def _line_contains_type_declaration(line: str, name: str, symbol_type: str) -> bool:
    """Guard against stale class/interface/trait rows."""
    if not line:
        return False
    token = str(symbol_type)
    if token == "class":
        token = "struct"
    elif token == "interface":
        token = "trait"
    return bool(re.search(rf"\b{re.escape(token)}\s+{re.escape(name)}\b", line))


def _collect_dynamic_reference_tokens(
    store: IndexStore, patterns: list[str]
) -> set[str]:
    """Collect class tokens from dynamic framework surfaces such as hooks and manifests."""
    tokens: set[str] = set()
    if not patterns:
        return tokens

    rows = store.conn.execute(
        "SELECT path FROM files WHERE language = 'php' ORDER BY path"
    ).fetchall()
    for row in rows:
        path = row["path"]
        normalized = path.replace("\\", "/")
        if not any(_path_matches_pattern(normalized, pattern) for pattern in patterns):
            continue
        content = _read_file_safe(store, path)
        if content is None:
            continue
        tokens.update(_extract_class_reference_tokens(content))
    return tokens


def _collect_runtime_class_references(
    store: IndexStore,
    *,
    allowed_languages: set[str],
) -> set[str]:
    """Collect class references from runtime PHP syntax missed by the dependency index."""
    tokens: set[str] = set()
    if "php" not in allowed_languages:
        return tokens

    rows = store.conn.execute(
        "SELECT path FROM files WHERE lower(language) = 'php' ORDER BY path"
    ).fetchall()
    for row in rows:
        content = _read_file_safe(store, row["path"])
        if content is None:
            continue
        tokens.update(_extract_runtime_php_class_references(content))
    return tokens


def _collect_runtime_string_class_references(
    store: IndexStore,
    *,
    allowed_languages: set[str],
    known_class_tokens: set[str],
) -> set[str]:
    tokens: set[str] = set()
    if "php" not in allowed_languages or not known_class_tokens:
        return tokens

    rows = store.conn.execute(
        "SELECT path FROM files WHERE lower(language) = 'php' ORDER BY path"
    ).fetchall()
    for row in rows:
        content = _read_file_safe(store, row["path"])
        if content is None:
            continue
        tokens.update(
            _extract_runtime_php_string_class_references(content, known_class_tokens)
        )
    return tokens


def _collect_rust_type_reference_map(store: IndexStore) -> dict[str, set[str]]:
    """Collect PascalCase type references from Rust files keyed by short name."""
    references: dict[str, set[str]] = defaultdict(set)
    rows = store.conn.execute(
        "SELECT path FROM files WHERE lower(language) = 'rust' ORDER BY path"
    ).fetchall()
    for row in rows:
        path = row["path"]
        content = _read_file_safe(store, path)
        if content is None:
            continue
        for token in set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", content)):
            references[token].add(path)
    return references


def _extract_class_reference_tokens(content: str) -> set[str]:
    """Extract likely class reference tokens from dynamic PHP surfaces."""
    tokens: set[str] = set()
    patterns = (
        r"\\?([A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+)::class\b",
        r"\b([A-Z][A-Za-z0-9_]*)::class\b",
        r"\b(?:new|extends|implements|instanceof)\s+\\?([A-Z][A-Za-z0-9_\\]*)\b",
        r"['\"](\\?[A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+)['\"]",
    )
    for pattern in patterns:
        for match in re.findall(pattern, content):
            token = str(match).lstrip("\\")
            if not token:
                continue
            tokens.add(token)
            tokens.add(token.rsplit("\\", 1)[-1])
    return tokens


def _extract_runtime_php_class_references(content: str) -> set[str]:
    """Extract class references from common runtime-only PHP constructs."""
    tokens: set[str] = set()
    namespace = _extract_php_namespace(content)

    for pattern in (
        r"(?<![\w\\])\\?([A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+)::class\b",
        r"\bnew\s+\\?([A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+)\b",
        r"#\[\s*\\?([A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+)\b",
    ):
        for match in re.findall(pattern, content):
            _register_runtime_class_reference(tokens, str(match), namespace=namespace)

    for pattern in (
        r"(?<![\w\\])([A-Z][A-Za-z0-9_]*)::class\b",
        r"\bnew\s+([A-Z][A-Za-z0-9_]*)\b",
        r"#\[\s*([A-Z][A-Za-z0-9_]*)\b",
    ):
        for match in re.findall(pattern, content):
            _register_runtime_class_reference(tokens, str(match), namespace=namespace)

    return tokens


def _extract_runtime_php_string_class_references(
    content: str,
    known_class_tokens: set[str],
) -> set[str]:
    tokens: set[str] = set()
    for match in re.findall(r"['\"]([A-Z][A-Za-z0-9_\\\\]{2,})['\"]", content):
        token = str(match).lstrip("\\")
        if token in known_class_tokens:
            tokens.add(token)
            if "\\" in token:
                tokens.add(token.rsplit("\\", 1)[-1])
    return tokens


def _register_runtime_class_reference(
    tokens: set[str],
    name: str,
    *,
    namespace: str | None,
) -> None:
    normalized = name.lstrip("\\")
    if not normalized:
        return
    tokens.add(normalized)
    if "\\" not in normalized and namespace:
        tokens.add(f"{namespace}\\{normalized}")


def _extract_php_namespace(content: str) -> str | None:
    match = re.search(r"^\s*namespace\s+([^;]+);", content, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()
