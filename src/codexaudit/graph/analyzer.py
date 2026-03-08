"""Graph analysis algorithms for codebase health assessment.

Implements cycle detection, coupling metrics, god class detection,
bottleneck identification, layer violation detection, and orphan file discovery.
"""

from __future__ import annotations

import logging
import time
from fnmatch import fnmatch
from pathlib import PurePosixPath

import networkx as nx

from codexaudit.indexer.store import IndexStore
from codexaudit.models import (
    ArchitecturalLayer,
    CouplingMetrics,
    GodClass,
    GraphAnalysisResult,
    LayerViolation,
)
from codexaudit.policy.models import GraphPolicy

logger = logging.getLogger(__name__)
_UNKNOWN_LAYER_WARNINGS: set[tuple[str, str]] = set()


# Architectural layer ordering (lower = deeper in the stack)
# Higher layers should not be imported by lower layers.
LAYER_ORDER: dict[ArchitecturalLayer, int] = {
    ArchitecturalLayer.VIEW: 0,
    ArchitecturalLayer.CONTROLLER: 1,
    ArchitecturalLayer.MIDDLEWARE: 1,
    ArchitecturalLayer.SERVICE: 2,
    ArchitecturalLayer.REPOSITORY: 3,
    ArchitecturalLayer.MODEL: 4,
    ArchitecturalLayer.CONFIG: 5,
    ArchitecturalLayer.UTILITY: 6,
    ArchitecturalLayer.MIGRATION: 7,
    ArchitecturalLayer.UNKNOWN: -1,
}

# Heuristic patterns for detecting architectural layers from file paths
LAYER_PATTERNS: dict[str, ArchitecturalLayer] = {
    "controller": ArchitecturalLayer.CONTROLLER,
    "controllers": ArchitecturalLayer.CONTROLLER,
    "service": ArchitecturalLayer.SERVICE,
    "services": ArchitecturalLayer.SERVICE,
    "model": ArchitecturalLayer.MODEL,
    "models": ArchitecturalLayer.MODEL,
    "repository": ArchitecturalLayer.REPOSITORY,
    "repositories": ArchitecturalLayer.REPOSITORY,
    "view": ArchitecturalLayer.VIEW,
    "views": ArchitecturalLayer.VIEW,
    "resources/views": ArchitecturalLayer.VIEW,
    "components": ArchitecturalLayer.VIEW,
    "pages": ArchitecturalLayer.VIEW,
    "config": ArchitecturalLayer.CONFIG,
    "migration": ArchitecturalLayer.MIGRATION,
    "migrations": ArchitecturalLayer.MIGRATION,
    "database/migrations": ArchitecturalLayer.MIGRATION,
    "middleware": ArchitecturalLayer.MIDDLEWARE,
    "middlewares": ArchitecturalLayer.MIDDLEWARE,
    "request": ArchitecturalLayer.CONTROLLER,
    "requests": ArchitecturalLayer.CONTROLLER,
    "helper": ArchitecturalLayer.UTILITY,
    "helpers": ArchitecturalLayer.UTILITY,
    "util": ArchitecturalLayer.UTILITY,
    "utils": ArchitecturalLayer.UTILITY,
}


def detect_layer_from_path(
    filepath: str,
    custom_patterns: dict[str, str] | None = None,
) -> ArchitecturalLayer:
    """Detect the architectural layer from a file path using heuristics."""
    path_lower = filepath.lower().replace("\\", "/")

    patterns = dict(LAYER_PATTERNS)
    if custom_patterns:
        for key, value in custom_patterns.items():
            try:
                patterns[key.lower()] = ArchitecturalLayer(value)
            except ValueError:
                warning_key = (key.lower(), str(value))
                if warning_key not in _UNKNOWN_LAYER_WARNINGS:
                    logger.debug(
                        "Ignoring unknown custom layer '%s' for pattern '%s'",
                        value,
                        key,
                    )
                    _UNKNOWN_LAYER_WARNINGS.add(warning_key)

    # Check longer patterns first (more specific)
    for pattern, layer in sorted(patterns.items(), key=lambda x: -len(x[0])):
        if f"/{pattern}/" in f"/{path_lower}/" or path_lower.startswith(f"{pattern}/"):
            return layer

    return ArchitecturalLayer.UNKNOWN


def find_circular_dependencies(
    graph: nx.DiGraph, max_length: int = 6
) -> list[list[str]]:
    """Find circular dependencies in the graph.

    Strategy for large graphs:
    1. Find strongly connected components (SCCs) - O(V+E)
    2. Only search for cycles within SCCs that have > 1 node
    3. Use length_bound to cap exponential blowup

    Returns a list of cycles, where each cycle is a list of file paths.
    """
    cycles = []

    # Step 1: Find non-trivial SCCs (fast, linear time)
    sccs = [scc for scc in nx.strongly_connected_components(graph) if len(scc) > 1]

    if not sccs:
        return []

    # Step 2: For each SCC, find cycles in the subgraph
    for scc in sorted(sccs, key=len, reverse=True):
        if len(cycles) >= 100:
            break

        subgraph = graph.subgraph(scc)

        # For very large SCCs, just report the SCC membership instead of enumerating cycles
        if len(scc) > 200:
            # Report as a single large cycle (the SCC itself)
            sample = sorted(scc)[:20]  # Show first 20 files
            cycles.append(sample)
            continue

        try:
            for cycle in nx.simple_cycles(subgraph, length_bound=max_length):
                if len(cycle) >= 2:
                    cycles.append(list(cycle))
                if len(cycles) >= 100:
                    break
        except nx.NetworkXError:
            pass

    return cycles


def _build_strong_dependency_graph(graph: nx.DiGraph) -> nx.DiGraph:
    strong_graph = nx.DiGraph()
    strong_graph.add_nodes_from(graph.nodes(data=True))

    for source, target, data in graph.edges(data=True):
        edge_types = data.get("types")
        if edge_types:
            if all(edge_type == "load" for edge_type in edge_types):
                continue
        elif data.get("type") == "load":
            continue
        strong_graph.add_edge(source, target, **data)

    return strong_graph


def calculate_coupling(graph: nx.DiGraph) -> list[CouplingMetrics]:
    """Calculate afferent (Ca) and efferent (Ce) coupling per directory/module.

    - Afferent coupling (Ca): number of incoming dependencies (other modules depend on this)
    - Efferent coupling (Ce): number of outgoing dependencies (this module depends on others)
    - Instability (I): Ce / (Ca + Ce), ranges from 0 (stable) to 1 (unstable)
    """
    # Group files by their top-level directory
    module_in: dict[str, set[str]] = {}  # module -> set of source modules
    module_out: dict[str, set[str]] = {}  # module -> set of target modules

    for node in graph.nodes:
        module = _get_module(node)
        if module not in module_in:
            module_in[module] = set()
        if module not in module_out:
            module_out[module] = set()

    for source, target in graph.edges:
        source_mod = _get_module(source)
        target_mod = _get_module(target)

        if source_mod != target_mod:
            module_out[source_mod].add(target_mod)
            module_in[target_mod].add(source_mod)

    metrics = []
    for module in sorted(set(module_in.keys()) | set(module_out.keys())):
        ca = len(module_in.get(module, set()))
        ce = len(module_out.get(module, set()))
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.0

        metrics.append(
            CouplingMetrics(
                module=module,
                afferent=ca,
                efferent=ce,
                instability=round(instability, 3),
            )
        )

    # Sort by instability descending
    metrics.sort(key=lambda m: m.instability, reverse=True)
    return metrics


def find_god_classes(
    store: IndexStore,
    min_methods: int = 15,
    min_deps: int = 10,
    min_lines_for_dependency_only: int = 200,
) -> list[GodClass]:
    """Find classes that are too large (high method count + high dependency count).

    Uses a single SQL query with JOIN to avoid N+1 queries.
    Default thresholds: 15+ methods, or a long single-class file with 10+ dependencies.
    Test files are excluded because they routinely aggregate fixtures and assertions
    in ways that are not meaningful production god-class signals.
    """
    # Single query with subqueries for method and dependency counts
    rows = store.conn.execute(
        """
        SELECT * FROM (
            SELECT
                s.name, s.line_start, s.line_end, s.file_id,
                f.path as file_path,
                (
                    SELECT COUNT(*) FROM symbols c
                    WHERE c.file_id = s.file_id AND c.type = 'class'
                ) as class_count,
                (SELECT COUNT(*) FROM symbols m
                 WHERE m.parent_symbol_id = s.id AND m.type = 'method') as method_count,
                (s.line_end - s.line_start) as line_count,
                (SELECT COUNT(*) FROM dependencies d
                 WHERE d.source_file_id = s.file_id
                   AND d.type NOT IN ('load', 'register')) as dep_count
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.type = 'class'
              AND lower(f.path) NOT LIKE '%/tests/%'
              AND lower(f.path) NOT LIKE '%/test/%'
              AND lower(f.path) NOT LIKE 'test_%'
              AND lower(f.path) NOT LIKE '%_test.py'
        )
        WHERE method_count >= ?
           OR (
                class_count = 1
                AND dep_count >= ?
                AND line_count >= ?
           )
        ORDER BY method_count DESC
    """,
        (min_methods, min_deps, min_lines_for_dependency_only),
    ).fetchall()

    return [
        GodClass(
            name=row["name"],
            file_path=row["file_path"],
            method_count=row["method_count"],
            dependency_count=row["dep_count"],
            line_count=row["line_count"],
        )
        for row in rows
    ]


def find_bottlenecks(graph: nx.DiGraph, top_n: int = 20) -> list[tuple[str, float]]:
    """Find bottleneck files using betweenness centrality.

    Files with high betweenness centrality are on many shortest paths,
    meaning changes to them have the highest blast radius.

    Uses approximate centrality (k-sampling) for large graphs.
    """
    if graph.number_of_nodes() < 3:
        return []

    try:
        n_nodes = graph.number_of_nodes()
        if n_nodes > 5000:
            # Approximate: sample sqrt(n) nodes for O(sqrt(n) * (V+E)) instead of O(V^3)
            k = min(500, max(100, int(n_nodes**0.5)))
            centrality = nx.betweenness_centrality(graph, k=k, weight="weight")
        else:
            centrality = nx.betweenness_centrality(graph, weight="weight")
    except nx.NetworkXError:
        return []

    # Sort by centrality descending and take top N
    sorted_nodes = sorted(centrality.items(), key=lambda x: x[1], reverse=True)
    return [
        (path, round(score, 4)) for path, score in sorted_nodes[:top_n] if score > 0
    ]


def detect_layer_violations(
    graph: nx.DiGraph,
    store: IndexStore | None = None,
    policy: GraphPolicy | None = None,
) -> list[LayerViolation]:
    """Detect dependency violations in the architectural layering.

    A violation occurs when a lower layer imports a higher layer, e.g.:
    - Model imports Controller
    - Service imports Controller
    - Repository imports Controller

    Uses path-based heuristics to detect layers. If envelope data is available
    in the store, it's used for more accurate layer detection.
    """
    # Build layer map from envelopes if available, otherwise use path heuristics
    layer_map: dict[str, ArchitecturalLayer] = {}

    if store is not None:
        try:
            envelopes_by_layer = store.get_envelopes_by_layer()
            for layer_name, items in envelopes_by_layer.items():
                layer = ArchitecturalLayer(layer_name)
                for item in items:
                    layer_map[item["file_path"]] = layer
        except Exception:
            pass

    # Fill in any missing entries with path-based detection
    for node in graph.nodes:
        if node not in layer_map:
            custom_patterns = policy.layer_patterns if policy else None
            layer_map[node] = detect_layer_from_path(
                node, custom_patterns=custom_patterns
            )

    violations = []
    for source, target in graph.edges:
        if policy and policy.layer_violation_excludes:
            if any(
                fnmatch(source, pat) or fnmatch(target, pat)
                for pat in policy.layer_violation_excludes
            ):
                continue

        source_layer = layer_map.get(source, ArchitecturalLayer.UNKNOWN)
        target_layer = layer_map.get(target, ArchitecturalLayer.UNKNOWN)

        # Skip unknown layers
        if (
            source_layer == ArchitecturalLayer.UNKNOWN
            or target_layer == ArchitecturalLayer.UNKNOWN
        ):
            continue

        source_order = LAYER_ORDER.get(source_layer, -1)
        target_order = LAYER_ORDER.get(target_layer, -1)

        # Violation: a deeper layer (higher order) imports a shallower layer (lower order)
        # E.g., Model (4) importing Controller (1) is wrong
        if source_order > target_order and target_order >= 0:
            violations.append(
                LayerViolation(
                    source_file=source,
                    source_layer=source_layer,
                    target_name=target,
                    target_layer=target_layer,
                    violation=f"{source_layer.value} -> {target_layer.value} (reversed dependency)",
                )
            )

    return violations


# Entry point patterns - files matching these are expected to have no incoming
# dependencies (controllers handle HTTP, commands handle CLI, etc.)
ENTRY_POINT_PATTERNS = [
    "/Commands/",
    "/Console/",
    "/Hooks/",
    "/hooks/",
    "/bootstrap/",
    "/config/",
    "/controllers/",
    "/Controllers/",
    "/routes/",
    "/migrations/",
    "/seeders/",
    "/factories/",
    "/tests/",
    "/Tests/",
    "/Middleware/",
]


def _is_default_entry_point(node: str) -> bool:
    return any(pat in f"/{node}" for pat in ENTRY_POINT_PATTERNS)


def _matches_orphan_entry_pattern(node: str, patterns: list[str]) -> bool:
    normalized = node.replace("\\", "/")
    return any(fnmatch(normalized, pattern) for pattern in patterns)


def _get_load_dependency_sources(store: IndexStore) -> set[str]:
    rows = store.conn.execute(
        """
        SELECT DISTINCT f.path
        FROM dependencies d
        JOIN files f ON f.id = d.source_file_id
        WHERE d.type = 'load'
        """
    ).fetchall()
    return {row["path"] for row in rows}


def find_orphan_files(
    graph: nx.DiGraph,
    store: IndexStore,
    policy: GraphPolicy | None = None,
) -> tuple[list[str], list[str]]:
    """Find files with zero incoming edges (potentially dead code).

    Splits known entry points and loader-driven files into a runtime-entry
    candidate bucket so they do not pollute the true orphan list.
    """
    orphans = []
    runtime_entry_candidates = []
    policy_patterns = policy.orphan_entry_patterns if policy else []
    load_sources = _get_load_dependency_sources(store)
    for node in graph.nodes:
        if graph.in_degree(node) == 0 and graph.out_degree(node) > 0:
            if (
                _is_default_entry_point(node)
                or node in load_sources
                or (
                    policy_patterns
                    and _matches_orphan_entry_pattern(node, policy_patterns)
                )
            ):
                runtime_entry_candidates.append(node)
                continue
            orphans.append(node)

    return sorted(orphans), sorted(runtime_entry_candidates)


def analyze_graph(
    graph: nx.DiGraph,
    store: IndexStore,
    policy: GraphPolicy | None = None,
) -> GraphAnalysisResult:
    """Run all graph analysis algorithms and return a comprehensive result."""
    t_total = time.monotonic()

    t0 = time.monotonic()
    cycles = find_circular_dependencies(graph)
    logger.info(
        "Circular dependencies: %d found in %.2fs", len(cycles), time.monotonic() - t0
    )

    t0 = time.monotonic()
    strong_cycles = find_circular_dependencies(_build_strong_dependency_graph(graph))
    logger.info(
        "Strong circular dependencies: %d found in %.2fs",
        len(strong_cycles),
        time.monotonic() - t0,
    )

    t0 = time.monotonic()
    coupling = calculate_coupling(graph)
    logger.info(
        "Coupling metrics: %d modules in %.2fs", len(coupling), time.monotonic() - t0
    )

    t0 = time.monotonic()
    god_classes = find_god_classes(store)
    logger.info(
        "God classes: %d found in %.2fs", len(god_classes), time.monotonic() - t0
    )

    t0 = time.monotonic()
    bottlenecks = find_bottlenecks(graph)
    logger.info(
        "Bottlenecks: %d found in %.2fs", len(bottlenecks), time.monotonic() - t0
    )

    t0 = time.monotonic()
    violations = detect_layer_violations(graph, store, policy=policy)
    logger.info(
        "Layer violations: %d found in %.2fs", len(violations), time.monotonic() - t0
    )

    t0 = time.monotonic()
    orphans, runtime_entry_candidates = find_orphan_files(graph, store, policy=policy)
    logger.info("Orphan files: %d found in %.2fs", len(orphans), time.monotonic() - t0)
    if runtime_entry_candidates:
        logger.info(
            "Runtime entry candidates: %d found in %.2fs",
            len(runtime_entry_candidates),
            time.monotonic() - t0,
        )

    density = nx.density(graph) if graph.number_of_nodes() > 0 else 0.0
    logger.info("Full graph analysis completed in %.2fs", time.monotonic() - t_total)

    return GraphAnalysisResult(
        circular_dependencies=cycles,
        strong_circular_dependencies=strong_cycles,
        coupling_metrics=coupling,
        god_classes=god_classes,
        bottleneck_files=bottlenecks,
        layer_violations=violations,
        orphan_files=orphans,
        runtime_entry_candidates=runtime_entry_candidates,
        node_count=graph.number_of_nodes(),
        edge_count=graph.number_of_edges(),
        density=round(density, 6),
    )


def _get_module(filepath: str) -> str:
    """Get the module from a file path.

    Uses 2-level depth for well-known framework directories (app/, resources/,
    database/, tests/) to provide meaningful granularity. Uses 1-level depth
    for everything else to avoid treating individual files as modules.
    """
    parts = PurePosixPath(filepath).parts
    if not parts:
        return "root"

    # For framework directories, use 2 levels of depth for better granularity
    if parts[0] in ("app", "resources", "database", "tests") and len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"

    # For everything else, use the first directory
    return parts[0]
