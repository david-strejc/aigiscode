from __future__ import annotations

from codexaudit.graph.builder import build_file_graph
from codexaudit.indexer.store import IndexStore
from codexaudit.models import (
    DependencyInfo,
    DependencyType,
    FileInfo,
    Language,
    SymbolInfo,
    SymbolType,
    Visibility,
)


def test_build_file_graph_resolves_python_module_and_symbol_imports(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    base_file_id = store.insert_file(
        FileInfo(path="pkg/base.py", language=Language.PYTHON, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=base_file_id,
                type=SymbolType.CLASS,
                name="BaseService",
                namespace="pkg.base",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=2,
            )
        ]
    )

    service_file_id = store.insert_file(
        FileInfo(path="pkg/service.py", language=Language.PYTHON, size=0)
    )
    store.insert_dependencies_batch(
        [
            DependencyInfo(
                source_file_id=service_file_id,
                target_name="pkg.base.BaseService",
                type=DependencyType.IMPORT,
                line=1,
            ),
            DependencyInfo(
                source_file_id=service_file_id,
                target_name="pkg.base",
                type=DependencyType.IMPORT,
                line=2,
            ),
        ]
    )

    graph = build_file_graph(store)

    assert graph.has_edge("pkg/service.py", "pkg/base.py")
    store.close()


def test_build_file_graph_resolves_php_load_suffix_targets(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    target_file_id = store.insert_file(
        FileInfo(
            path="wp-admin/includes/class-wp-site-health.php",
            language=Language.PHP,
            size=0,
        )
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=target_file_id,
                type=SymbolType.CLASS,
                name="WP_Site_Health",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=10,
            )
        ]
    )

    source_file_id = store.insert_file(
        FileInfo(path="wp-admin/site-health.php", language=Language.PHP, size=0)
    )
    store.insert_dependencies_batch(
        [
            DependencyInfo(
                source_file_id=source_file_id,
                target_name="wp-admin/includes/class-wp-site-health.php",
                type=DependencyType.LOAD,
                line=1,
            )
        ]
    )

    graph = build_file_graph(store)

    assert graph.has_edge(
        "wp-admin/site-health.php",
        "wp-admin/includes/class-wp-site-health.php",
    )
    store.close()


def test_build_file_graph_resolves_ruby_namespaces_and_require_relative(
    tmp_path,
) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    engine_file_id = store.insert_file(
        FileInfo(path="lib/spina/engine.rb", language=Language.RUBY, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=engine_file_id,
                type=SymbolType.CLASS,
                name="Engine",
                namespace="Spina",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=10,
            )
        ]
    )

    plugin_file_id = store.insert_file(
        FileInfo(path="lib/spina/plugin.rb", language=Language.RUBY, size=0)
    )
    store.insert_dependencies_batch(
        [
            DependencyInfo(
                source_file_id=plugin_file_id,
                target_name="Spina::Engine",
                type=DependencyType.IMPORT,
                line=1,
            ),
            DependencyInfo(
                source_file_id=plugin_file_id,
                target_name="engine",
                type=DependencyType.LOAD,
                line=2,
            ),
        ]
    )

    graph = build_file_graph(store)

    assert graph.has_edge("lib/spina/plugin.rb", "lib/spina/engine.rb")
    store.close()


def test_build_file_graph_resolves_ruby_lexical_namespace_constants(tmp_path) -> None:
    store = IndexStore(tmp_path / ".codexaudit" / "codexaudit.db")
    store.initialize()

    image_file_id = store.insert_file(
        FileInfo(path="app/models/spina/image.rb", language=Language.RUBY, size=0)
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=image_file_id,
                type=SymbolType.CLASS,
                name="Image",
                namespace="Spina",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=10,
            )
        ]
    )

    controller_file_id = store.insert_file(
        FileInfo(
            path="app/controllers/spina/admin/images_controller.rb",
            language=Language.RUBY,
            size=0,
        )
    )
    store.insert_symbols_batch(
        [
            SymbolInfo(
                file_id=controller_file_id,
                type=SymbolType.CLASS,
                name="ImagesController",
                namespace="Spina::Admin",
                visibility=Visibility.PUBLIC,
                line_start=1,
                line_end=20,
            )
        ]
    )
    store.insert_dependencies_batch(
        [
            DependencyInfo(
                source_file_id=controller_file_id,
                target_name="Image",
                type=DependencyType.IMPORT,
                line=1,
            )
        ]
    )

    graph = build_file_graph(store)

    assert graph.has_edge(
        "app/controllers/spina/admin/images_controller.rb",
        "app/models/spina/image.rb",
    )
    store.close()
