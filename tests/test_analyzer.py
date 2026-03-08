from __future__ import annotations

import networkx as nx

from codexaudit.graph.analyzer import (
    _build_strong_dependency_graph,
    detect_layer_from_path,
    find_god_classes,
    find_orphan_files,
)
from codexaudit.indexer.store import IndexStore
from codexaudit.models import (
    ArchitecturalLayer,
    FileInfo,
    Language,
    SymbolInfo,
    SymbolType,
    Visibility,
)


def test_detect_layer_from_path_recognizes_middleware() -> None:
    assert (
        detect_layer_from_path("app/Http/Middleware/Authenticate.php")
        == ArchitecturalLayer.MIDDLEWARE
    )


def test_find_god_classes_ignores_small_multi_class_files_and_tests(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    noisy_file_id = store.insert_file(
        FileInfo(path="pkg/helpers.py", language=Language.PYTHON, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=noisy_file_id,
                type=SymbolType.CLASS,
                name="FirstHelper",
                namespace="pkg.helpers",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=80,
            ),
            SymbolInfo(
                file_id=noisy_file_id,
                type=SymbolType.CLASS,
                name="SecondHelper",
                namespace="pkg.helpers",
                visibility=Visibility.PUBLIC,
                line_start=81,
                line_end=160,
            ),
        ]
    )
    for index in range(12):
        store.conn.execute(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'import', ?)",
            (noisy_file_id, f"pkg.dep{index}", index + 1),
        )

    test_file_id = store.insert_file(
        FileInfo(path="tests/test_large.py", language=Language.PYTHON, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=test_file_id,
                type=SymbolType.CLASS,
                name="LargeTestCase",
                namespace="tests.test_large",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=400,
            )
        ]
    )
    for index in range(20):
        store.conn.execute(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'import', ?)",
            (test_file_id, f"tests.dep{index}", index + 1),
        )
    store.conn.commit()

    assert find_god_classes(store) == []
    store.close()


def test_find_god_classes_ignores_load_and_register_edges(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    file_id = store.insert_file(
        FileInfo(path="app/Kernel.php", language=Language.PHP, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=file_id,
                type=SymbolType.CLASS,
                name="Kernel",
                namespace="App",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=280,
            )
        ]
    )
    for index in range(12):
        store.conn.execute(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'register', ?)",
            (file_id, f"App\\\\Listener{index}", index + 1),
        )
    for index in range(5):
        store.conn.execute(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'load', ?)",
            (file_id, f"bootstrap/file{index}.php", 20 + index),
        )
    store.conn.commit()

    assert find_god_classes(store) == []
    store.close()


def test_find_orphan_files_splits_runtime_entry_candidates(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    entry_file_id = store.insert_file(
        FileInfo(path="src/index.php", language=Language.PHP, size=0)
    )
    store.insert_file(FileInfo(path="src/bootstrap.php", language=Language.PHP, size=0))
    orphan_file_id = store.insert_file(
        FileInfo(path="src/legacy_runner.php", language=Language.PHP, size=0)
    )
    store.insert_file(FileInfo(path="src/service.php", language=Language.PHP, size=0))

    store.conn.execute(
        "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'load', 1)",
        (entry_file_id, "bootstrap.php"),
    )
    store.conn.execute(
        "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, 'import', 1)",
        (orphan_file_id, "Service"),
    )
    store.conn.commit()

    graph = nx.DiGraph()
    graph.add_edge("src/index.php", "src/bootstrap.php")
    graph.add_edge("src/legacy_runner.php", "src/service.php")

    orphan_files, runtime_entry_candidates = find_orphan_files(graph, store)

    assert orphan_files == ["src/legacy_runner.php"]
    assert runtime_entry_candidates == ["src/index.php"]


def test_build_strong_dependency_graph_ignores_load_only_edges() -> None:
    graph = nx.DiGraph()
    graph.add_edge("a.php", "b.php", type="load", types=["load"], weight=1)
    graph.add_edge("b.php", "a.php", type="inherit", types=["inherit"], weight=1)
    graph.add_edge("b.php", "c.php", type="import", types=["load", "import"], weight=2)

    strong_graph = _build_strong_dependency_graph(graph)

    assert not strong_graph.has_edge("a.php", "b.php")
    assert strong_graph.has_edge("b.php", "a.php")
    assert strong_graph.has_edge("b.php", "c.php")
