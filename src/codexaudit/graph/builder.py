"""Build a NetworkX dependency graph from indexed data.

Uses direct SQL queries for performance on large codebases (20K+ files).
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import networkx as nx

from codexaudit.indexer.store import IndexStore
from codexaudit.policy.models import GraphPolicy

logger = logging.getLogger(__name__)


def build_file_graph(
    store: IndexStore, policy: GraphPolicy | None = None
) -> nx.DiGraph:
    """Build a directed graph where nodes are files and edges are dependencies.

    Optimized for large codebases: uses SQL aggregation instead of
    loading all symbols/dependencies into Python.
    """
    graph = nx.DiGraph()
    conn = store.conn

    # Add all files as nodes with aggregated symbol counts (single query)
    rows = conn.execute("""
        SELECT f.id, f.path, f.language, f.size,
               COUNT(s.id) as symbol_count
        FROM files f
        LEFT JOIN symbols s ON s.file_id = f.id
        GROUP BY f.id
    """).fetchall()

    file_map: dict[int, str] = {}
    for row in rows:
        file_map[row["id"]] = row["path"]
        graph.add_node(
            row["path"],
            language=row["language"],
            size=row["size"],
            symbol_count=row["symbol_count"],
        )

    logger.info(f"Graph: {len(file_map)} file nodes")

    # Build name-to-file lookup using SQL (classes, interfaces, traits, functions)
    # Only index "important" symbols for dependency resolution
    name_rows = conn.execute("""
        SELECT s.name, s.namespace, s.type, f.path, f.language
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type IN ('class', 'module', 'interface', 'trait', 'enum', 'function')
    """).fetchall()

    name_to_files: dict[str, list[str]] = {}
    ruby_namespaces_by_file: dict[str, set[str]] = {}
    for row in name_rows:
        name = row["name"]
        path = row["path"]
        namespace = row["namespace"]
        language = row["language"]
        symbol_type = row["type"]

        # Map by simple name
        name_to_files.setdefault(name, [])
        if path not in name_to_files[name]:
            name_to_files[name].append(path)

        # Map by FQN
        if namespace:
            if language == "python":
                fqn = f"{namespace}.{name}"
            elif language == "ruby":
                fqn = f"{namespace}::{name}"
            else:
                fqn = f"{namespace}\\{name}"
            name_to_files.setdefault(fqn, [])
            if path not in name_to_files[fqn]:
                name_to_files[fqn].append(path)

        if language == "ruby":
            ruby_namespaces_by_file.setdefault(path, set())
            if namespace:
                ruby_namespaces_by_file[path].add(namespace)
            elif symbol_type == "module":
                ruby_namespaces_by_file[path].add(name)

    logger.info(f"Graph: {len(name_to_files)} symbol name mappings")

    # Pre-build a set of all known file paths for fast JS/TS import matching
    # Map stem -> [paths] for quick filename matching
    stem_to_paths: dict[str, list[str]] = {}
    module_to_paths: dict[str, list[str]] = {}
    filename_to_paths: dict[str, list[str]] = {}
    for path in file_map.values():
        pure_path = PurePosixPath(path)
        stem = pure_path.stem
        stem_to_paths.setdefault(stem, [])
        stem_to_paths[stem].append(path)
        filename_to_paths.setdefault(pure_path.name, [])
        filename_to_paths[pure_path.name].append(path)
        module_name = _python_module_name_for_path(path)
        if module_name:
            module_to_paths.setdefault(module_name, [])
            module_to_paths[module_name].append(path)

    # Load dependencies and resolve targets
    dep_rows = conn.execute("""
        SELECT d.source_file_id, d.target_name, d.type
        FROM dependencies d
    """).fetchall()

    logger.info(f"Graph: resolving {len(dep_rows)} dependencies")

    if policy is None:
        policy = GraphPolicy()

    edge_weights: dict[tuple[str, str, str], int] = {}
    all_node_paths = set(graph.nodes)

    for row in dep_rows:
        source_path = file_map.get(row["source_file_id"])
        if not source_path:
            continue

        target_name = row["target_name"]
        dep_type = row["type"]

        # Resolve target to file paths
        target_paths = _resolve_target(
            target_name,
            source_path=source_path,
            name_to_files=name_to_files,
            stem_to_paths=stem_to_paths,
            filename_to_paths=filename_to_paths,
            module_to_paths=module_to_paths,
            ruby_namespaces_by_file=ruby_namespaces_by_file,
            all_node_paths=all_node_paths,
            policy=policy,
        )

        for target_path in target_paths:
            if target_path == source_path:
                continue
            if target_path not in all_node_paths:
                continue

            key = (source_path, target_path, dep_type)
            edge_weights[key] = edge_weights.get(key, 0) + 1

    # Add edges
    for (source, target, dep_type), weight in edge_weights.items():
        if graph.has_edge(source, target):
            existing = graph[source][target]
            types = set(existing.get("types", []))
            types.add(dep_type)
            existing["types"] = list(types)
            existing["weight"] = existing.get("weight", 0) + weight
        else:
            graph.add_edge(
                source, target, type=dep_type, types=[dep_type], weight=weight
            )

    logger.info(
        f"Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
    )
    return graph


def _resolve_target(
    target_name: str,
    source_path: str,
    name_to_files: dict[str, list[str]],
    stem_to_paths: dict[str, list[str]],
    filename_to_paths: dict[str, list[str]],
    module_to_paths: dict[str, list[str]],
    ruby_namespaces_by_file: dict[str, set[str]],
    all_node_paths: set[str],
    policy: GraphPolicy,
) -> list[str]:
    """Resolve a dependency target name to file paths."""
    # Direct match on full name (PHP FQN or simple name)
    direct_matches = _select_unique_paths(name_to_files.get(target_name, []))
    if direct_matches:
        return direct_matches

    # Try short name from PHP namespace
    if "\\" in target_name:
        short_name = target_name.rsplit("\\", 1)[-1]
        short_matches = _select_unique_paths(name_to_files.get(short_name, []))
        if short_matches:
            return short_matches
    if "::" in target_name:
        short_name = target_name.rsplit("::", 1)[-1]
        short_matches = _select_unique_paths(name_to_files.get(short_name, []))
        if short_matches:
            return short_matches

    ruby_constant_matches = _resolve_ruby_constant_target(
        target_name,
        source_path=source_path,
        name_to_files=name_to_files,
        ruby_namespaces_by_file=ruby_namespaces_by_file,
    )
    if ruby_constant_matches:
        return ruby_constant_matches

    python_matches = _resolve_python_import(target_name, module_to_paths)
    if python_matches:
        return python_matches

    ruby_load_matches = _resolve_ruby_load_target(
        target_name, source_path, all_node_paths
    )
    if ruby_load_matches:
        return ruby_load_matches

    php_load_matches = _resolve_php_load_target(
        target_name, filename_to_paths, all_node_paths
    )
    if php_load_matches:
        return php_load_matches

    # JS/TS imports: resolve as module paths first.
    js_matches = _resolve_js_import(target_name, source_path, all_node_paths, policy)
    if js_matches:
        return js_matches

    # Optional fuzzy fallback for mixed repos with incomplete import paths.
    if policy.js_fuzzy_import_resolution and "/" in target_name:
        clean = target_name.lstrip("@./")
        segments = clean.split("/")
        last = segments[-1] if segments else target_name

        if last in stem_to_paths:
            return stem_to_paths[last]

        if len(clean) > 3:
            matches = [p for p in all_node_paths if clean in p]
            if matches:
                return matches[:5]

    return []


def _select_unique_paths(paths: list[str]) -> list[str]:
    unique = list(dict.fromkeys(paths))
    if len(unique) == 1:
        return unique
    return []


def _resolve_php_load_target(
    target_name: str,
    filename_to_paths: dict[str, list[str]],
    all_node_paths: set[str],
) -> list[str]:
    normalized = target_name.strip().replace("\\", "/").lstrip("/")
    if not normalized or not normalized.endswith(".php"):
        return []

    if normalized in all_node_paths:
        return [normalized]

    if normalized.startswith(("wp-", "class-", "IXR/")):
        matches = [path for path in all_node_paths if path.endswith(normalized)]
        if len(matches) == 1:
            return matches

    filename = PurePosixPath(normalized).name
    direct = filename_to_paths.get(filename, [])
    if len(direct) == 1:
        return direct

    suffix_matches = [path for path in all_node_paths if path.endswith(normalized)]
    if len(suffix_matches) == 1:
        return suffix_matches

    return []


def _resolve_ruby_load_target(
    target_name: str,
    source_path: str,
    all_node_paths: set[str],
) -> list[str]:
    normalized = target_name.strip().replace("\\", "/")
    if not normalized:
        return []

    candidates: list[str] = []
    if normalized.startswith(("./", "../")):
        source_dir = PurePosixPath(source_path).parent
        relative_target = PurePosixPath(source_dir, normalized)
        if relative_target.suffix == ".rb":
            candidates.append(str(relative_target))
        else:
            candidates.append(f"{relative_target}.rb")
    else:
        if normalized.endswith(".rb"):
            candidates.append(normalized.lstrip("/"))
        else:
            candidates.append(f"{normalized.lstrip('/')}.rb")

    for candidate in candidates:
        normalized_candidate = _normalize_posix_path(candidate)
        if normalized_candidate in all_node_paths:
            return [normalized_candidate]

    for candidate in candidates:
        normalized_candidate = _normalize_posix_path(candidate)
        suffix_matches = [
            path
            for path in all_node_paths
            if path == normalized_candidate or path.endswith(f"/{normalized_candidate}")
        ]
        if len(suffix_matches) == 1:
            return suffix_matches

    return []


def _resolve_ruby_constant_target(
    target_name: str,
    source_path: str,
    name_to_files: dict[str, list[str]],
    ruby_namespaces_by_file: dict[str, set[str]],
) -> list[str]:
    if not target_name or any(sep in target_name for sep in ("/", ".")):
        return []

    if "::" in target_name:
        return _select_unique_paths(name_to_files.get(target_name, []))

    namespaces = sorted(
        ruby_namespaces_by_file.get(source_path, ()), key=len, reverse=True
    )
    for namespace in namespaces:
        parts = [part for part in namespace.split("::") if part]
        while parts:
            candidate = "::".join([*parts, target_name])
            matches = _select_unique_paths(name_to_files.get(candidate, []))
            if matches:
                return matches
            parts.pop()

    return _select_unique_paths(name_to_files.get(target_name, []))


def _python_module_name_for_path(path: str) -> str | None:
    pure_path = PurePosixPath(path)
    if pure_path.suffix != ".py":
        return None

    parts = list(pure_path.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def _resolve_python_import(
    target_name: str,
    module_to_paths: dict[str, list[str]],
) -> list[str]:
    if target_name in module_to_paths:
        return module_to_paths[target_name]

    candidate = target_name
    while "." in candidate:
        candidate = candidate.rsplit(".", 1)[0]
        if candidate in module_to_paths:
            return module_to_paths[candidate]

    return []


_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".vue")


def _resolve_js_import(
    target_name: str,
    source_path: str,
    all_node_paths: set[str],
    policy: GraphPolicy,
) -> list[str]:
    raw = target_name.strip()
    if not raw:
        return []

    normalized = raw
    for alias, mapped in policy.js_import_aliases.items():
        if raw.startswith(alias):
            normalized = f"{mapped}{raw[len(alias) :]}"
            break

    if normalized.startswith("./") or normalized.startswith("../"):
        base_dir = str(PurePosixPath(source_path).parent)
        normalized = str(PurePosixPath(base_dir) / normalized)

    normalized = normalized.replace("\\", "/")

    candidates: list[str] = []
    base = normalized.rstrip("/")
    if not base:
        return []

    if PurePosixPath(base).suffix:
        candidates.append(base)
    else:
        candidates.extend(base + ext for ext in _JS_EXTENSIONS)
        candidates.extend(
            str(PurePosixPath(base) / f"index{ext}") for ext in _JS_EXTENSIONS
        )

    # Preserve deterministic order and uniqueness.
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in all_node_paths:
            return [candidate]

    return []


def _normalize_posix_path(path: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts).as_posix()
