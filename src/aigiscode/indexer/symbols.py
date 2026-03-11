"""Symbol extraction from tree-sitter parse trees.

Extracts classes, methods, functions, properties, imports, and other
structural elements from parsed source files.
"""

from __future__ import annotations

import ast
import logging
import re
import warnings
from typing import TYPE_CHECKING

from aigiscode.models import (
    DependencyInfo,
    DependencyType,
    SymbolInfo,
    SymbolType,
    Visibility,
)

if TYPE_CHECKING:
    from tree_sitter import Node


logger = logging.getLogger(__name__)


def _text(node: Node | None) -> str:
    """Get the text content of a tree-sitter node."""
    if node is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _find_child(node: Node, type_name: str) -> Node | None:
    """Find the first child of a given type."""
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _find_children(node: Node, type_name: str) -> list[Node]:
    """Find all children of a given type."""
    return [child for child in node.children if child.type == type_name]


def _get_visibility(node: Node) -> Visibility:
    """Extract visibility modifier from a node."""
    for child in node.children:
        if child.type == "visibility_modifier":
            text = _text(child).lower()
            if text in {"public", "pub"}:
                return Visibility.PUBLIC
            elif text == "protected":
                return Visibility.PROTECTED
            elif text == "private":
                return Visibility.PRIVATE
    return Visibility.UNKNOWN


def _get_rust_visibility(
    node: Node,
    *,
    default: Visibility = Visibility.PRIVATE,
) -> Visibility:
    """Extract Rust visibility, defaulting to Rust's private-by-default semantics."""
    modifier = _find_child(node, "visibility_modifier")
    if modifier is None:
        return default
    return Visibility.PUBLIC if _text(modifier).startswith("pub") else default


def _extract_namespace_name(node: Node) -> str:
    """Extract the full namespace name from a namespace_definition or namespace_name node."""
    ns_name = _find_child(node, "namespace_name")
    if ns_name:
        return _text(ns_name)
    return ""


# --- PHP Symbol Extraction ---


def extract_php_symbols(
    root_node: Node,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols and dependencies from a PHP parse tree."""
    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []
    current_namespace = ""

    def walk(
        node: Node,
        parent_class_name: str | None = None,
        parent_symbol_idx: int | None = None,
    ) -> None:
        nonlocal current_namespace

        if node.type == "namespace_definition":
            current_namespace = _extract_namespace_name(node)
            # Continue into the namespace body
            for child in node.children:
                walk(child, parent_class_name, parent_symbol_idx)
            return

        if node.type == "namespace_use_declaration":
            _extract_php_use(node, dependencies)
            return

        if node.type == "class_declaration":
            _extract_php_class(node, symbols, dependencies, current_namespace)
            return

        if node.type == "interface_declaration":
            _extract_php_interface(node, symbols, current_namespace)
            return

        if node.type == "trait_declaration":
            _extract_php_trait(node, symbols, current_namespace)
            return

        if node.type == "enum_declaration":
            _extract_php_enum(node, symbols, current_namespace)
            return

        if node.type == "function_definition":
            name_node = _find_child(node, "name")
            if name_node:
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.FUNCTION,
                        name=_text(name_node),
                        namespace=current_namespace or None,
                        visibility=Visibility.PUBLIC,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        metadata=_extract_function_metadata(node),
                    )
                )
            return

        for child in node.children:
            walk(child, parent_class_name, parent_symbol_idx)

    walk(root_node)
    logger.debug(
        "PHP extraction: %d symbols, %d dependencies", len(symbols), len(dependencies)
    )
    return symbols, dependencies


def extract_php_runtime_dependencies(source_code: str) -> list[DependencyInfo]:
    """Extract runtime PHP dependencies missed by the tree-sitter symbol pass."""
    dependencies: list[DependencyInfo] = []
    dependencies.extend(_extract_php_include_dependencies(source_code))
    dependencies.extend(_extract_php_callback_class_dependencies(source_code))
    dependencies.extend(_extract_php_registration_dependencies(source_code))
    return _dedupe_dependencies(dependencies)


def _extract_php_use(node: Node, dependencies: list[DependencyInfo]) -> None:
    """Extract PHP use (import) statements."""
    for clause in _find_children(node, "namespace_use_clause"):
        qn = _find_child(clause, "qualified_name")
        if qn:
            target = _text(qn)
            dependencies.append(
                DependencyInfo(
                    target_name=target,
                    type=DependencyType.IMPORT,
                    line=node.start_point[0] + 1,
                )
            )


def _extract_php_include_dependencies(source_code: str) -> list[DependencyInfo]:
    dependencies: list[DependencyInfo] = []
    pattern = re.compile(
        r"\b(?:require|require_once|include|include_once)\b(?P<expr>[^;]+);",
        re.IGNORECASE,
    )
    for match in pattern.finditer(source_code):
        expr = match.group("expr")
        literal_path = _normalize_php_include_expr(expr)
        if not literal_path:
            continue
        dependencies.append(
            DependencyInfo(
                target_name=literal_path,
                type=DependencyType.LOAD,
                line=source_code[: match.start()].count("\n") + 1,
            )
        )
    return dependencies


def _extract_php_callback_class_dependencies(source_code: str) -> list[DependencyInfo]:
    dependencies: list[DependencyInfo] = []
    patterns = (
        re.compile(
            r"(?:array\s*\(|\[)\s*['\"](?P<class>[A-Z][A-Za-z0-9_\\]+)['\"]\s*,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]",
        ),
        re.compile(
            r"\b(?:class_exists|interface_exists|trait_exists|enum_exists|is_a|is_subclass_of)\s*\(\s*['\"](?P<class>[A-Z][A-Za-z0-9_\\]+)['\"]",
        ),
        re.compile(
            r"\b[_a-zA-Z0-9]*?(?:load|make|create|resolve|get)[_a-zA-Z0-9]*\s*\(\s*['\"](?P<class>[A-Z][A-Za-z0-9_\\]+)['\"]",
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(source_code):
            dependencies.append(
                DependencyInfo(
                    target_name=match.group("class").lstrip("\\"),
                    type=DependencyType.IMPORT,
                    line=source_code[: match.start()].count("\n") + 1,
                )
            )
    return dependencies


_PHP_FUNCTION_CALLBACK_CALLEES = {
    "add_action",
    "add_filter",
    "add_shortcode",
    "add_menu_page",
    "add_submenu_page",
    "register_activation_hook",
    "register_deactivation_hook",
    "register_shutdown_function",
    "register_uninstall_hook",
    "set_error_handler",
    "set_exception_handler",
}


def _extract_php_registration_dependencies(source_code: str) -> list[DependencyInfo]:
    dependencies: list[DependencyInfo] = []

    for callee, args_source, line in _find_php_call_expressions(source_code):
        if not _looks_like_registration_callee(callee):
            continue

        args = _split_php_top_level_args(args_source)
        if not args:
            continue

        for target_name in _extract_php_registration_class_targets(args):
            dependencies.append(
                DependencyInfo(
                    target_name=target_name,
                    type=DependencyType.REGISTER,
                    line=line,
                )
            )

        if _uses_function_callback_targets(callee):
            for target_name in _extract_php_function_callback_targets(args):
                dependencies.append(
                    DependencyInfo(
                        target_name=target_name,
                        type=DependencyType.REGISTER,
                        line=line,
                    )
                )

    return dependencies


def _find_php_call_expressions(source_code: str) -> list[tuple[str, str, int]]:
    call_pattern = re.compile(
        r"(?P<callee>(?:[$A-Za-z_\\][A-Za-z0-9_$\\]*\s*(?:(?:::|->)\s*[A-Za-z_][A-Za-z0-9_]*)?|[A-Za-z_][A-Za-z0-9_]*))\s*\(",
    )
    calls: list[tuple[str, str, int]] = []

    for match in call_pattern.finditer(source_code):
        open_paren_index = match.end() - 1
        args_source, close_index = _extract_balanced_parenthesized(
            source_code, open_paren_index
        )
        if args_source is None or close_index is None:
            continue
        callee = re.sub(r"\s+", "", match.group("callee"))
        calls.append(
            (callee, args_source, source_code[: match.start()].count("\n") + 1)
        )

    return calls


def _extract_balanced_parenthesized(
    source_code: str, open_paren_index: int
) -> tuple[str | None, int | None]:
    if open_paren_index < 0 or open_paren_index >= len(source_code):
        return None, None
    if source_code[open_paren_index] != "(":
        return None, None

    depth = 0
    quote: str | None = None
    escaped = False

    for index in range(open_paren_index, len(source_code)):
        char = source_code[index]

        if quote is not None:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return source_code[open_paren_index + 1 : index], index

    return None, None


def _split_php_top_level_args(args_source: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False

    for char in args_source:
        if quote is not None:
            current.append(char)
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue

        if char in "([{":
            depth += 1
            current.append(char)
            continue

        if char in ")]}":
            depth = max(depth - 1, 0)
            current.append(char)
            continue

        if char == "," and depth == 0:
            arg = "".join(current).strip()
            if arg:
                args.append(arg)
            current = []
            continue

        current.append(char)

    tail = "".join(current).strip()
    if tail:
        args.append(tail)

    return args


def _looks_like_registration_callee(callee: str) -> bool:
    short_name = _normalize_php_callee_name(callee)
    lowered = short_name.lower()
    lowered_callee = callee.lower()
    return (
        lowered in _PHP_FUNCTION_CALLBACK_CALLEES
        or "\\route::" in lowered_callee
        or "route::" in lowered_callee
        or "router->" in lowered_callee
        or "router::" in lowered_callee
        or lowered.startswith("register")
        or lowered.startswith("listen")
        or lowered.startswith("subscribe")
        or lowered.startswith("attach")
        or lowered.startswith("bind")
        or lowered.startswith("hook")
        or lowered.startswith("observe")
        or lowered.startswith("schedule")
        or lowered.startswith("command")
        or lowered.startswith("route")
        or lowered.startswith("on")
        or lowered.endswith("handler")
        or lowered.endswith("callback")
    )


def _normalize_php_callee_name(callee: str) -> str:
    if "->" in callee:
        return callee.rsplit("->", 1)[-1]
    if "::" in callee:
        return callee.rsplit("::", 1)[-1]
    if "\\" in callee:
        return callee.rsplit("\\", 1)[-1]
    return callee


def _extract_php_registration_class_targets(args: list[str]) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()

    for arg in args:
        for match in re.finditer(
            r"\b(?P<class>[A-Z][A-Za-z0-9_\\]+)::class\b",
            arg,
        ):
            class_name = match.group("class").lstrip("\\")
            if class_name not in seen:
                seen.add(class_name)
                targets.append(class_name)

        callback_match = re.search(
            r"(?:array\s*\(|\[)\s*(?:(?P<class1>[A-Z][A-Za-z0-9_\\]+)::class|['\"](?P<class2>[A-Z][A-Za-z0-9_\\]+)['\"])\s*,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]",
            arg,
        )
        if not callback_match:
            continue

        class_name = callback_match.group("class1") or callback_match.group("class2")
        if class_name and class_name not in seen:
            seen.add(class_name)
            targets.append(class_name.lstrip("\\"))

    return targets


def _uses_function_callback_targets(callee: str) -> bool:
    return _normalize_php_callee_name(callee).lower() in _PHP_FUNCTION_CALLBACK_CALLEES


def _extract_php_function_callback_targets(args: list[str]) -> list[str]:
    if len(args) < 2:
        return []

    targets: list[str] = []
    seen: set[str] = set()
    for arg in args[1:]:
        match = re.fullmatch(r"""['"](?P<name>[A-Za-z_][A-Za-z0-9_]*)['"]""", arg)
        if not match:
            continue
        function_name = match.group("name")
        if function_name not in seen:
            seen.add(function_name)
            targets.append(function_name)
    return targets


def _normalize_php_include_expr(expr: str) -> str | None:
    segments = re.findall(r"""['"]([^'"]+?\.php)['"]""", expr)
    if not segments:
        return None
    candidate = "".join(segment.strip() for segment in segments if segment.strip())
    if not candidate:
        candidate = segments[-1].strip()
    normalized = candidate.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.lstrip("/")


def _dedupe_dependencies(
    dependencies: list[DependencyInfo],
) -> list[DependencyInfo]:
    seen: set[tuple[str, str, int]] = set()
    deduped: list[DependencyInfo] = []
    for dep in dependencies:
        key = (dep.type.value, dep.target_name, dep.line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dep)
    return deduped


def _extract_php_class(
    node: Node,
    symbols: list[SymbolInfo],
    dependencies: list[DependencyInfo],
    namespace: str,
) -> None:
    """Extract a PHP class declaration with its members."""
    name_node = _find_child(node, "name")
    if not name_node:
        return

    class_name = _text(name_node)
    metadata: dict = {}

    # Extract parent class (extends)
    base_clause = _find_child(node, "base_clause")
    if base_clause:
        for child in base_clause.children:
            if child.type in ("name", "qualified_name"):
                parent_name = _text(child)
                metadata["extends"] = parent_name
                dependencies.append(
                    DependencyInfo(
                        target_name=parent_name,
                        type=DependencyType.INHERIT,
                        line=node.start_point[0] + 1,
                    )
                )

    # Extract interfaces (implements)
    interface_clause = _find_child(node, "class_interface_clause")
    if interface_clause:
        interfaces = []
        for child in interface_clause.children:
            if child.type in ("name", "qualified_name"):
                iface_name = _text(child)
                interfaces.append(iface_name)
                dependencies.append(
                    DependencyInfo(
                        target_name=iface_name,
                        type=DependencyType.IMPLEMENT,
                        line=node.start_point[0] + 1,
                    )
                )
        if interfaces:
            metadata["implements"] = interfaces

    class_symbol = SymbolInfo(
        type=SymbolType.CLASS,
        name=class_name,
        namespace=namespace or None,
        visibility=Visibility.PUBLIC,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        metadata=metadata,
    )
    symbols.append(class_symbol)

    # Extract members from declaration_list
    # Note: parent_symbol_id is set to None here; class membership is tracked
    # via the "class" key in metadata. The DB FK will be resolved post-insert
    # in a future version.
    decl_list = _find_child(node, "declaration_list")
    if decl_list:
        for child in decl_list.children:
            if child.type == "method_declaration":
                _extract_php_method(child, symbols, class_name, namespace)
            elif child.type == "property_declaration":
                _extract_php_property(child, symbols, class_name, namespace)


def _extract_php_method(
    node: Node,
    symbols: list[SymbolInfo],
    class_name: str,
    namespace: str,
) -> None:
    """Extract a PHP method declaration."""
    name_node = _find_child(node, "name")
    if not name_node:
        return

    metadata = _extract_function_metadata(node)
    metadata["class"] = class_name

    symbols.append(
        SymbolInfo(
            type=SymbolType.METHOD,
            name=_text(name_node),
            namespace=namespace or None,
            visibility=_get_visibility(node),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            metadata=metadata,
        )
    )


def _extract_php_property(
    node: Node,
    symbols: list[SymbolInfo],
    class_name: str,
    namespace: str,
) -> None:
    """Extract a PHP property declaration."""
    # Property can have multiple declarators
    for child in node.children:
        if child.type == "property_element":
            var_node = _find_child(child, "variable_name")
            if var_node:
                prop_name = _text(var_node).lstrip("$")
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.PROPERTY,
                        name=prop_name,
                        namespace=namespace or None,
                        visibility=_get_visibility(node),
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        metadata={"class": class_name},
                    )
                )


def _extract_php_interface(
    node: Node, symbols: list[SymbolInfo], namespace: str
) -> None:
    """Extract a PHP interface declaration."""
    name_node = _find_child(node, "name")
    if name_node:
        symbols.append(
            SymbolInfo(
                type=SymbolType.INTERFACE,
                name=_text(name_node),
                namespace=namespace or None,
                visibility=Visibility.PUBLIC,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            )
        )


def _extract_php_trait(node: Node, symbols: list[SymbolInfo], namespace: str) -> None:
    """Extract a PHP trait declaration."""
    name_node = _find_child(node, "name")
    if name_node:
        symbols.append(
            SymbolInfo(
                type=SymbolType.TRAIT,
                name=_text(name_node),
                namespace=namespace or None,
                visibility=Visibility.PUBLIC,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            )
        )


def _extract_php_enum(node: Node, symbols: list[SymbolInfo], namespace: str) -> None:
    """Extract a PHP enum declaration."""
    name_node = _find_child(node, "name")
    if name_node:
        symbols.append(
            SymbolInfo(
                type=SymbolType.ENUM,
                name=_text(name_node),
                namespace=namespace or None,
                visibility=Visibility.PUBLIC,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
            )
        )


def _extract_function_metadata(node: Node) -> dict:
    """Extract parameter and return type info from a function/method node."""
    metadata: dict = {}

    # Parameters
    params_node = _find_child(node, "formal_parameters")
    if params_node:
        params = []
        for param in params_node.children:
            if param.type in (
                "simple_parameter",
                "property_promotion_parameter",
                "variadic_parameter",
            ):
                param_text = _text(param).strip()
                if param_text:
                    params.append(param_text)
        if params:
            metadata["params"] = params

    # Return type
    for child in node.children:
        if child.type in (
            "union_type",
            "named_type",
            "primitive_type",
            "nullable_type",
            "intersection_type",
        ):
            metadata["return_type"] = _text(child)
            break
        # Check for return type after ':'
        if child.type == ":":
            # Next sibling is the return type
            idx = node.children.index(child)
            if idx + 1 < len(node.children):
                ret_node = node.children[idx + 1]
                if ret_node.type not in ("compound_statement", "{"):
                    metadata["return_type"] = _text(ret_node)
            break

    return metadata


# --- Python Symbol Extraction ---


def extract_python_symbols(
    source_code: str,
    module_name: str,
    package_name: str,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols and dependencies from Python source code."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        logger.warning("Python parse error in %s: %s", module_name or "<module>", exc)
        return [], []

    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            dependencies.extend(_extract_python_import(node))
            continue

        if isinstance(node, ast.ImportFrom):
            dependencies.extend(
                _extract_python_from_import(node, module_name, package_name)
            )
            continue

        if isinstance(node, ast.ClassDef):
            _extract_python_class(node, symbols, dependencies, module_name)
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                SymbolInfo(
                    type=SymbolType.FUNCTION,
                    name=node.name,
                    namespace=module_name or None,
                    visibility=Visibility.PUBLIC,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    metadata=_extract_python_function_metadata(node),
                )
            )

    logger.debug(
        "Python extraction: %d symbols, %d dependencies",
        len(symbols),
        len(dependencies),
    )
    return symbols, dependencies


def _extract_python_import(node: ast.Import) -> list[DependencyInfo]:
    dependencies: list[DependencyInfo] = []
    for alias in node.names:
        dependencies.append(
            DependencyInfo(
                target_name=alias.name,
                type=DependencyType.IMPORT,
                line=node.lineno,
            )
        )
    return dependencies


def _extract_python_from_import(
    node: ast.ImportFrom,
    module_name: str,
    package_name: str,
) -> list[DependencyInfo]:
    dependencies: list[DependencyInfo] = []
    base_module = _resolve_python_module_path(
        module_name=module_name,
        package_name=package_name,
        level=node.level,
        imported_module=node.module,
    )

    for alias in node.names:
        target_name = base_module
        if alias.name != "*" and alias.name:
            target_name = ".".join(part for part in (base_module, alias.name) if part)
        if not target_name:
            continue
        dependencies.append(
            DependencyInfo(
                target_name=target_name,
                type=DependencyType.IMPORT,
                line=node.lineno,
            )
        )
    return dependencies


def _resolve_python_module_path(
    module_name: str,
    package_name: str,
    level: int,
    imported_module: str | None,
) -> str:
    if level <= 0:
        return imported_module or ""

    base_parts = [part for part in package_name.split(".") if part]
    trim = max(level - 1, 0)
    if trim:
        base_parts = base_parts[: max(len(base_parts) - trim, 0)]

    imported_parts = [part for part in (imported_module or "").split(".") if part]
    return ".".join([*base_parts, *imported_parts])


def _extract_python_class(
    node: ast.ClassDef,
    symbols: list[SymbolInfo],
    dependencies: list[DependencyInfo],
    module_name: str,
) -> None:
    symbols.append(
        SymbolInfo(
            type=SymbolType.CLASS,
            name=node.name,
            namespace=module_name or None,
            visibility=Visibility.PUBLIC,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
        )
    )

    for base in node.bases:
        base_name = _python_expr_name(base)
        if not base_name:
            continue
        dependencies.append(
            DependencyInfo(
                target_name=base_name,
                type=DependencyType.INHERIT,
                line=node.lineno,
            )
        )

    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                SymbolInfo(
                    type=SymbolType.METHOD,
                    name=child.name,
                    namespace=module_name or None,
                    visibility=_get_python_visibility(child.name),
                    line_start=child.lineno,
                    line_end=child.end_lineno or child.lineno,
                    metadata={
                        "class": node.name,
                        **_extract_python_function_metadata(child),
                    },
                )
            )
            continue

        if isinstance(child, ast.Assign):
            for target in child.targets:
                property_name = _python_assignment_name(target)
                if not property_name:
                    continue
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.PROPERTY,
                        name=property_name,
                        namespace=module_name or None,
                        visibility=Visibility.PUBLIC,
                        line_start=child.lineno,
                        line_end=child.end_lineno or child.lineno,
                        metadata={"class": node.name},
                    )
                )
            continue

        if isinstance(child, ast.AnnAssign):
            property_name = _python_assignment_name(child.target)
            if not property_name:
                continue
            symbols.append(
                SymbolInfo(
                    type=SymbolType.PROPERTY,
                    name=property_name,
                    namespace=module_name or None,
                    visibility=Visibility.PUBLIC,
                    line_start=child.lineno,
                    line_end=child.end_lineno or child.lineno,
                    metadata={"class": node.name},
                )
            )


def _extract_python_function_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict:
    params: list[str] = []
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        params.append(arg.arg)
    if node.args.vararg:
        params.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        params.append(f"**{node.args.kwarg.arg}")

    metadata: dict[str, object] = {}
    if params:
        metadata["params"] = params

    return_type = _python_expr_name(node.returns)
    if return_type:
        metadata["return_type"] = return_type

    return metadata


def _python_expr_name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_expr_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        return _python_expr_name(node.value)
    if isinstance(node, ast.Call):
        return _python_expr_name(node.func)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Tuple):
        parts = [_python_expr_name(child) for child in node.elts]
        normalized = [part for part in parts if part]
        return ", ".join(normalized) if normalized else None
    return None


def _python_assignment_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _get_python_visibility(name: str) -> Visibility:
    if name.startswith("__") and not name.endswith("__"):
        return Visibility.PRIVATE
    if name.startswith("_"):
        return Visibility.PROTECTED
    return Visibility.PUBLIC


# --- Ruby Symbol Extraction ---


def extract_ruby_symbols(
    root_node: Node,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols and dependencies from a Ruby parse tree."""
    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []

    def walk(
        node: Node,
        current_scope: str | None = None,
        owner_name: str | None = None,
    ) -> None:
        if node.type == "module":
            raw_name = _extract_ruby_declared_name(node)
            if not raw_name:
                return

            namespace, symbol_name, full_name = _ruby_namespace_parts(
                raw_name,
                current_scope=current_scope,
            )
            symbols.append(
                SymbolInfo(
                    type=SymbolType.MODULE,
                    name=symbol_name,
                    namespace=namespace or None,
                    visibility=Visibility.PUBLIC,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                )
            )

            body = _find_child(node, "body_statement")
            if body:
                for child in body.children:
                    walk(child, current_scope=full_name, owner_name=symbol_name)
            return

        if node.type == "class":
            raw_name = _extract_ruby_declared_name(node)
            if not raw_name:
                return

            namespace, symbol_name, full_name = _ruby_namespace_parts(
                raw_name,
                current_scope=current_scope,
            )
            metadata: dict[str, object] = {}
            parent_name = _extract_ruby_superclass_name(node)
            if parent_name:
                metadata["extends"] = parent_name
                dependencies.append(
                    DependencyInfo(
                        target_name=parent_name,
                        type=DependencyType.INHERIT,
                        line=node.start_point[0] + 1,
                    )
                )

            symbols.append(
                SymbolInfo(
                    type=SymbolType.CLASS,
                    name=symbol_name,
                    namespace=namespace or None,
                    visibility=Visibility.PUBLIC,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    metadata=metadata,
                )
            )

            body = _find_child(node, "body_statement")
            if body:
                for child in body.children:
                    walk(child, current_scope=full_name, owner_name=symbol_name)
            return

        if node.type == "method":
            name_node = _find_child(node, "identifier")
            if not name_node:
                return

            metadata = _extract_ruby_method_metadata(node)
            symbol_type = SymbolType.FUNCTION
            if owner_name:
                symbol_type = SymbolType.METHOD
                metadata["class"] = owner_name

            symbols.append(
                SymbolInfo(
                    type=symbol_type,
                    name=_text(name_node),
                    namespace=current_scope or None,
                    visibility=Visibility.PUBLIC,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    metadata=metadata,
                )
            )
            body = _find_child(node, "body_statement")
            if body:
                for child in body.children:
                    walk(child, current_scope=current_scope, owner_name=owner_name)
            return

        if node.type == "call":
            should_descend = _extract_ruby_call_dependencies(node, dependencies)
            if not should_descend:
                return

        if node.type in {"constant", "scope_resolution"}:
            target_name = _normalize_ruby_constant_name(_text(node))
            if _should_collect_ruby_constant_dependency(node, target_name):
                dependencies.append(
                    DependencyInfo(
                        target_name=target_name,
                        type=DependencyType.IMPORT,
                        line=node.start_point[0] + 1,
                    )
                )
            return

        for child in node.children:
            walk(child, current_scope=current_scope, owner_name=owner_name)

    walk(root_node)
    dependencies = _dedupe_dependencies(dependencies)
    logger.debug(
        "Ruby extraction: %d symbols, %d dependencies",
        len(symbols),
        len(dependencies),
    )
    return symbols, dependencies


def _extract_ruby_declared_name(node: Node) -> str:
    for child in node.children:
        if child.type in {"constant", "scope_resolution"}:
            return _normalize_ruby_constant_name(_text(child))
    return ""


def _extract_ruby_superclass_name(node: Node) -> str | None:
    superclass = _find_child(node, "superclass")
    if superclass is None:
        return None
    for child in superclass.children:
        if child.type in {"constant", "scope_resolution"}:
            name = _normalize_ruby_constant_name(_text(child))
            if name:
                return name
    return None


def _extract_ruby_call_dependencies(
    node: Node,
    dependencies: list[DependencyInfo],
) -> bool:
    callee = _extract_ruby_call_name(node)
    if not callee:
        return True

    if callee in {"require", "require_relative", "require_dependency"}:
        target_name = _extract_ruby_string_argument(node)
        if target_name:
            dependencies.append(
                DependencyInfo(
                    target_name=target_name,
                    type=DependencyType.LOAD,
                    line=node.start_point[0] + 1,
                )
            )
        return False

    if callee not in {"include", "extend", "prepend"}:
        return True

    target_name = _extract_ruby_constant_argument(node)
    if not target_name:
        return False

    dependencies.append(
        DependencyInfo(
            target_name=target_name,
            type=DependencyType.IMPLEMENT,
            line=node.start_point[0] + 1,
        )
    )
    return False


def _extract_ruby_call_name(node: Node) -> str:
    children = node.children
    if not children:
        return ""
    if children[0].type == "identifier":
        return _text(children[0])
    if len(children) >= 3 and children[2].type == "identifier":
        return _text(children[2])
    return ""


def _extract_ruby_string_argument(node: Node) -> str:
    argument_list = _find_child(node, "argument_list")
    if argument_list is None:
        return ""
    string_node = _find_child(argument_list, "string")
    if string_node is None:
        return ""
    return _extract_ruby_string_value(string_node)


def _extract_ruby_constant_argument(node: Node) -> str:
    argument_list = _find_child(node, "argument_list")
    if argument_list is None:
        return ""
    for child in argument_list.children:
        if child.type in {"constant", "scope_resolution"}:
            return _normalize_ruby_constant_name(_text(child))
    return ""


def _extract_ruby_string_value(node: Node) -> str:
    for child in node.children:
        if child.type == "string_content":
            return _text(child)
    return _text(node).strip("'\"")


def _normalize_ruby_constant_name(value: str) -> str:
    return value.strip().lstrip(":").strip()


def _ruby_namespace_parts(
    raw_name: str,
    current_scope: str | None,
) -> tuple[str, str, str]:
    normalized = _normalize_ruby_constant_name(raw_name)
    if "::" in normalized:
        parts = [part for part in normalized.split("::") if part]
        full_name = "::".join(parts)
    elif current_scope:
        full_name = f"{current_scope}::{normalized}"
        parts = [part for part in full_name.split("::") if part]
    else:
        full_name = normalized
        parts = [normalized]

    if not parts:
        return "", normalized, normalized
    if len(parts) == 1:
        return "", parts[0], parts[0]
    return "::".join(parts[:-1]), parts[-1], "::".join(parts)


def _extract_ruby_method_metadata(node: Node) -> dict:
    metadata: dict[str, object] = {}
    params_node = _find_child(node, "method_parameters")
    if params_node is None:
        return metadata

    params: list[str] = []
    for child in params_node.children:
        if child.type in {"identifier", "optional_parameter", "splat_parameter"}:
            text = _text(child).strip()
            if text:
                params.append(text)
    if params:
        metadata["params"] = params
    return metadata


def _should_collect_ruby_constant_dependency(node: Node, target_name: str) -> bool:
    if not target_name:
        return False

    parent = node.parent
    if parent is not None:
        if parent.type == "scope_resolution" and node.type == "constant":
            return False
        if parent.type in {"module", "class", "superclass"}:
            return False

    return target_name not in {"ENV"}


# --- Rust Symbol Extraction ---


def extract_rust_symbols(
    root_node: Node,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols and dependencies from a Rust parse tree."""
    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []

    def walk(
        node: Node,
        current_owner: str | None = None,
        current_owner_kind: str | None = None,
    ) -> None:
        if node.type == "use_declaration":
            for target_name in _expand_rust_use_paths(_text(node)[4:].rstrip(";").strip()):
                dependencies.append(
                    DependencyInfo(
                        target_name=target_name,
                        type=DependencyType.IMPORT,
                        line=node.start_point[0] + 1,
                    )
                )
            return

        if node.type == "mod_item":
            name_node = _find_child(node, "identifier")
            if name_node is not None:
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.MODULE,
                        name=_text(name_node),
                        visibility=_get_rust_visibility(node),
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                    )
                )
            return

        if node.type == "struct_item":
            name_node = _find_child(node, "type_identifier")
            if name_node is None:
                return
            struct_name = _text(name_node)
            symbols.append(
                SymbolInfo(
                    type=SymbolType.CLASS,
                    name=struct_name,
                    visibility=_get_rust_visibility(node),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                )
            )
            fields = _find_child(node, "field_declaration_list")
            if fields is not None:
                for field in _find_children(fields, "field_declaration"):
                    field_name = _find_child(field, "field_identifier")
                    if field_name is None:
                        continue
                    symbols.append(
                        SymbolInfo(
                            type=SymbolType.PROPERTY,
                            name=_text(field_name),
                            namespace=struct_name,
                            visibility=_get_rust_visibility(field),
                            line_start=field.start_point[0] + 1,
                            line_end=field.end_point[0] + 1,
                        )
                    )
            return

        if node.type == "enum_item":
            name_node = _find_child(node, "type_identifier")
            if name_node is not None:
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.ENUM,
                        name=_text(name_node),
                        visibility=_get_rust_visibility(node),
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                    )
                )
            return

        if node.type == "trait_item":
            name_node = _find_child(node, "type_identifier")
            if name_node is None:
                return
            trait_name = _text(name_node)
            symbols.append(
                SymbolInfo(
                    type=SymbolType.INTERFACE,
                    name=trait_name,
                    visibility=_get_rust_visibility(node),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                )
            )
            for child in node.children:
                walk(
                    child,
                    current_owner=trait_name,
                    current_owner_kind="trait",
                )
            return

        if node.type == "impl_item":
            target_name = None
            identifiers = [
                _text(child)
                for child in node.children
                if child.type == "type_identifier"
            ]
            has_for = any(child.type == "for" for child in node.children)
            if has_for and len(identifiers) >= 2:
                dependencies.append(
                    DependencyInfo(
                        target_name=identifiers[0],
                        type=DependencyType.IMPLEMENT,
                        line=node.start_point[0] + 1,
                    )
                )
                target_name = identifiers[1]
            elif identifiers:
                target_name = identifiers[0]
            for child in node.children:
                walk(
                    child,
                    current_owner=target_name,
                    current_owner_kind="impl",
                )
            return

        if node.type in {"function_item", "function_signature_item"}:
            name_node = _find_child(node, "identifier")
            if name_node is None:
                return
            metadata = _extract_rust_function_metadata(node)
            symbol_type = SymbolType.METHOD if current_owner else SymbolType.FUNCTION
            visibility = (
                Visibility.PUBLIC
                if current_owner_kind == "trait"
                else _get_rust_visibility(node)
            )
            symbols.append(
                SymbolInfo(
                    type=symbol_type,
                    name=_text(name_node),
                    namespace=current_owner or None,
                    visibility=visibility,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    metadata=metadata,
                )
            )
            return

        for child in node.children:
            walk(
                child,
                current_owner=current_owner,
                current_owner_kind=current_owner_kind,
            )

    walk(root_node)
    dependencies = _dedupe_dependencies(dependencies)
    logger.debug(
        "Rust extraction: %d symbols, %d dependencies",
        len(symbols),
        len(dependencies),
    )
    return symbols, dependencies


def _extract_rust_function_metadata(node: Node) -> dict[str, object]:
    metadata: dict[str, object] = {}
    params_node = _find_child(node, "parameters")
    if params_node is None:
        return metadata

    params: list[str] = []
    has_self_parameter = False
    for child in params_node.children:
        if child.type in {"self_parameter", "parameter", "identifier"}:
            text = _text(child).strip()
            if text:
                params.append(text)
                if "self" in text:
                    has_self_parameter = True
    if params:
        metadata["params"] = params
    if has_self_parameter:
        metadata["has_self_parameter"] = True
    return metadata


def _expand_rust_use_paths(path: str) -> list[str]:
    path = path.strip()
    if not path:
        return []
    path = re.sub(r"\s+as\s+[A-Za-z_][A-Za-z0-9_]*$", "", path)
    if "{" not in path:
        return [path.replace(" ", "")]

    brace_start = path.find("{")
    brace_end = path.rfind("}")
    if brace_start == -1 or brace_end == -1 or brace_end < brace_start:
        return [path.replace(" ", "")]

    prefix = path[:brace_start].rstrip(":")
    inner = path[brace_start + 1 : brace_end]
    expanded: list[str] = []
    for item in _split_rust_use_items(inner):
        item = item.strip()
        if not item:
            continue
        if item == "self":
            expanded.append(prefix)
            continue
        candidate = item
        if prefix and not item.startswith(("crate::", "self::", "super::")):
            candidate = f"{prefix}::{item}"
        expanded.extend(_expand_rust_use_paths(candidate))
    return [item for item in expanded if item]


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


# --- TypeScript/JavaScript Symbol Extraction ---


def extract_ts_symbols(
    root_node: Node,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols and dependencies from a TypeScript/JavaScript parse tree."""
    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []

    def walk(node: Node) -> None:
        if node.type == "import_statement":
            _extract_ts_import(node, dependencies)
            return

        if node.type == "export_statement":
            _extract_ts_export(node, symbols, dependencies)
            # Still walk children for class/function definitions
            for child in node.children:
                if child.type in (
                    "class_declaration",
                    "function_declaration",
                    "lexical_declaration",
                ):
                    walk(child)
            return

        if node.type == "class_declaration":
            _extract_ts_class(node, symbols)
            return

        if node.type == "function_declaration":
            name_node = _find_child(node, "identifier")
            if name_node:
                symbols.append(
                    SymbolInfo(
                        type=SymbolType.FUNCTION,
                        name=_text(name_node),
                        visibility=Visibility.PUBLIC,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                    )
                )
            return

        for child in node.children:
            walk(child)

    walk(root_node)
    logger.debug(
        "TS/JS extraction: %d symbols, %d dependencies", len(symbols), len(dependencies)
    )
    return symbols, dependencies


def _extract_ts_import(node: Node, dependencies: list[DependencyInfo]) -> None:
    """Extract TypeScript/JavaScript import statements."""
    # Find the source string
    source_node = _find_child(node, "string")
    if not source_node:
        return

    # Extract the string content (remove quotes)
    source = _text(source_node).strip("'\"")

    dependencies.append(
        DependencyInfo(
            target_name=source,
            type=DependencyType.IMPORT,
            line=node.start_point[0] + 1,
        )
    )


def _extract_ts_export(
    node: Node,
    symbols: list[SymbolInfo],
    dependencies: list[DependencyInfo],
) -> None:
    """Extract TypeScript/JavaScript export statements."""
    # Check for re-exports: export { ... } from '...'
    source_node = _find_child(node, "string")
    if source_node:
        source = _text(source_node).strip("'\"")
        dependencies.append(
            DependencyInfo(
                target_name=source,
                type=DependencyType.IMPORT,
                line=node.start_point[0] + 1,
            )
        )


def _extract_ts_class(node: Node, symbols: list[SymbolInfo]) -> None:
    """Extract a TypeScript class declaration."""
    name_node = _find_child(node, "type_identifier") or _find_child(node, "identifier")
    if not name_node:
        return

    class_name = _text(name_node)
    metadata: dict = {}

    # Check for extends
    heritage = _find_child(node, "class_heritage")
    if heritage:
        extends_clause = _find_child(heritage, "extends_clause")
        if extends_clause:
            for child in extends_clause.children:
                if child.type in ("identifier", "type_identifier"):
                    metadata["extends"] = _text(child)

    symbols.append(
        SymbolInfo(
            type=SymbolType.CLASS,
            name=class_name,
            visibility=Visibility.PUBLIC,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            metadata=metadata,
        )
    )

    # Extract class body members
    body = _find_child(node, "class_body")
    if body:
        for child in body.children:
            if child.type == "method_definition":
                mname = (
                    _find_child(child, "private_property_identifier")
                    or _find_child(child, "property_identifier")
                    or _find_child(child, "identifier")
                )
                if mname:
                    symbols.append(
                        SymbolInfo(
                            type=SymbolType.METHOD,
                            name=_normalize_ts_member_name(_text(mname)),
                            visibility=_get_ts_visibility(child),
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            metadata={"class": class_name},
                        )
                    )
            elif child.type in ("public_field_definition", "field_definition"):
                fname = (
                    _find_child(child, "private_property_identifier")
                    or _find_child(child, "property_identifier")
                    or _find_child(child, "identifier")
                )
                if fname:
                    symbols.append(
                        SymbolInfo(
                            type=SymbolType.PROPERTY,
                            name=_normalize_ts_member_name(_text(fname)),
                            visibility=_get_ts_visibility(child),
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            metadata={"class": class_name},
                        )
                    )


def _get_ts_visibility(node: Node) -> Visibility:
    """Get visibility from TypeScript accessibility modifier."""
    if _find_child(node, "private_property_identifier") is not None:
        return Visibility.PRIVATE
    for child in node.children:
        if child.type == "accessibility_modifier":
            text = _text(child).lower()
            if text == "public":
                return Visibility.PUBLIC
            elif text == "protected":
                return Visibility.PROTECTED
            elif text == "private":
                return Visibility.PRIVATE
    return Visibility.PUBLIC  # Default in TS is public


def _normalize_ts_member_name(name: str) -> str:
    return name.lstrip("#")


# --- Vue Symbol Extraction ---


def extract_vue_symbols(
    root_node: Node, source_code: bytes
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Extract symbols from a Vue SFC.

    Vue files are parsed as HTML by tree-sitter. We find the <script> block
    and then re-parse its content as TypeScript.
    """
    symbols: list[SymbolInfo] = []
    dependencies: list[DependencyInfo] = []

    # Find script elements in the Vue template
    script_content, script_offset = _find_vue_script_block(root_node, source_code)

    if script_content:
        # Parse the script content as TypeScript
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                from tree_sitter_languages import get_parser

                ts_parser = get_parser("typescript")
            tree = ts_parser.parse(script_content.encode("utf-8"))
            ts_symbols, ts_deps = extract_ts_symbols(tree.root_node)

            # Adjust line numbers by the script block offset
            for sym in ts_symbols:
                sym.line_start += script_offset
                sym.line_end += script_offset
            for dep in ts_deps:
                dep.line += script_offset

            symbols.extend(ts_symbols)
            dependencies.extend(ts_deps)
        except Exception as exc:
            logger.debug("Falling back to Vue-only symbol extraction: %s", exc)

    return symbols, dependencies


def _find_vue_script_block(
    root_node: Node, source_code: bytes
) -> tuple[str | None, int]:
    """Find and extract the <script> block content from a Vue SFC parse tree.

    Returns (content, line_offset) or (None, 0) if no script block found.
    """

    # Walk the tree looking for script_element or element with tag name "script"
    def find_script(node: Node) -> tuple[str | None, int]:
        if node.type in ("script_element", "element"):
            # Check if this is a script tag
            start_tag = _find_child(node, "start_tag")
            if start_tag:
                tag_name = _find_child(start_tag, "tag_name")
                if tag_name and _text(tag_name) == "script":
                    # Find the raw_text content
                    raw = _find_child(node, "raw_text")
                    if raw:
                        return _text(raw), raw.start_point[0]

        for child in node.children:
            result = find_script(child)
            if result[0] is not None:
                return result

        return None, 0

    return find_script(root_node)
