from __future__ import annotations

from pathlib import Path

from aigiscode.graph.deadcode import (
    _extract_runtime_php_class_references,
    analyze_dead_code,
    find_abandoned_classes,
)
from aigiscode.indexer.parser import parse_file
from aigiscode.indexer.store import IndexStore
from aigiscode.models import (
    DependencyInfo,
    DependencyType,
    FileInfo,
    Language,
    SymbolInfo,
    SymbolType,
    Visibility,
)
from aigiscode.policy.models import DeadCodePolicy


def _make_store(project_root: Path) -> IndexStore:
    store = IndexStore(project_root / ".aigiscode" / "aigiscode.db")
    store.initialize()
    return store


def _write(project_root: Path, relative_path: str, content: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _index_parsed_file(
    store: IndexStore,
    project_root: Path,
    relative_path: str,
    language: Language,
) -> int:
    file_path = project_root / relative_path
    symbols, dependencies = parse_file(
        file_path,
        language,
        project_root=project_root,
    )
    file_id = store.insert_file(
        FileInfo(path=relative_path, language=language, size=file_path.stat().st_size)
    )
    for symbol in symbols:
        store.insert_symbol(symbol.model_copy(update={"file_id": file_id}))
    for dependency in dependencies:
        store.insert_dependency(dependency.model_copy(update={"source_file_id": file_id}))
    return file_id


def test_dead_code_skips_stale_index_rows(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/Example.php",
        "<?php\nnamespace App;\n\nclass Example\n{\n    private function usedHelper(): void {}\n    private string $currentValue = '';\n}\n",
    )
    example_file_id = store.insert_file(
        FileInfo(path="app/Example.php", language=Language.PHP, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=example_file_id,
            type=SymbolType.METHOD,
            name="oldHelper",
            visibility=Visibility.PRIVATE,
            line_start=5,
            line_end=5,
            metadata={},
        )
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=example_file_id,
            type=SymbolType.PROPERTY,
            name="oldValue",
            visibility=Visibility.PRIVATE,
            line_start=6,
            line_end=6,
            metadata={},
        )
    )
    store.insert_dependency(
        DependencyInfo(
            source_file_id=example_file_id,
            target_name="App\\Support\\MissingThing",
            type=DependencyType.IMPORT,
            line=2,
        )
    )

    _write(
        project_root,
        "app/Ghost.php",
        "<?php\nnamespace App;\n\nclass RealThing {}\n",
    )
    ghost_file_id = store.insert_file(
        FileInfo(path="app/Ghost.php", language=Language.PHP, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=ghost_file_id,
            type=SymbolType.CLASS,
            name="Ghost",
            namespace="App",
            line_start=4,
            line_end=4,
            metadata={},
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.total == 0
    store.close()


def test_abandoned_class_is_rescued_by_dynamic_reference_surface(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/Hooks/User.hooks.php",
        "<?php\nreturn [\\App\\Hooks\\SyncUser::class];\n",
    )
    store.insert_file(
        FileInfo(path="app/Hooks/User.hooks.php", language=Language.PHP, size=0)
    )

    _write(
        project_root,
        "app/Hooks/SyncUser.php",
        "<?php\nnamespace App\\Hooks;\n\nclass SyncUser {}\n",
    )
    sync_file_id = store.insert_file(
        FileInfo(path="app/Hooks/SyncUser.php", language=Language.PHP, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=sync_file_id,
            type=SymbolType.CLASS,
            name="SyncUser",
            namespace="App\\Hooks",
            line_start=4,
            line_end=4,
            metadata={},
        )
    )

    findings = find_abandoned_classes(
        store,
        allowed_languages=["php"],
        dynamic_reference_patterns=["**/*.hooks.php"],
    )

    assert findings == []
    store.close()


def test_abandoned_class_ignores_non_php_languages_by_default(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/WebSocketError.ts",
        "export class WebSocketError extends Error {}\n",
    )
    file_id = store.insert_file(
        FileInfo(
            path="resources/js/WebSocketError.ts",
            language=Language.TYPESCRIPT,
            size=0,
        )
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=file_id,
            type=SymbolType.CLASS,
            name="WebSocketError",
            line_start=1,
            line_end=1,
            metadata={},
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.abandoned_classes == []
    store.close()


def test_rust_dead_code_detects_unused_imports_methods_properties_and_public_types(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "src/lib.rs",
        "use crate::http::{Client as HttpClient, Server};\n"
        "pub struct Service { cache: Cache, local: Local }\n"
        "pub struct Exported {}\n"
        "impl Service {\n"
        "    pub fn run(&self) -> usize { let _ = self.helper(); self.local.len() }\n"
        "    fn helper(&self) -> HttpClient { HttpClient::new() }\n"
        "    fn stale(&self) {}\n"
        "}\n",
    )
    _index_parsed_file(store, project_root, "src/lib.rs", Language.RUST)

    _write(
        project_root,
        "src/consumer.rs",
        "use crate::lib::Service;\n"
        "pub fn consume(service: &Service) -> usize { service.run() }\n",
    )
    _index_parsed_file(store, project_root, "src/consumer.rs", Language.RUST)

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert [finding.name for finding in result.unused_imports] == ["crate::http::Server"]
    assert [finding.name for finding in result.unused_methods] == ["stale"]
    assert [finding.name for finding in result.unused_properties] == ["cache"]
    assert [finding.name for finding in result.abandoned_classes] == ["Exported"]
    store.close()


def test_unused_import_respects_top_level_route_code(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "routes/web.php",
        "<?php\n\nuse Illuminate\\Support\\Facades\\Route;\n\nRoute::get('/health', function () {\n    return 'ok';\n});\n",
    )
    file_id = store.insert_file(
        FileInfo(path="routes/web.php", language=Language.PHP, size=0)
    )
    store.insert_dependency(
        DependencyInfo(
            source_file_id=file_id,
            target_name="Illuminate\\Support\\Facades\\Route",
            type=DependencyType.IMPORT,
            line=3,
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.unused_imports == []
    store.close()


def test_runtime_php_class_references_include_same_namespace_symbols() -> None:
    content = """<?php
namespace App\\Modules\\Demo;

#[SyncAttribute]
final class Runner
{
    public function run(): void
    {
        $job = new SyncJob();
        $remote = \\App\\Shared\\RemoteWorker::class;
    }
}
"""

    tokens = _extract_runtime_php_class_references(content)

    assert "SyncJob" in tokens
    assert "App\\Modules\\Demo\\SyncJob" in tokens
    assert "SyncAttribute" in tokens
    assert "App\\Modules\\Demo\\SyncAttribute" in tokens
    assert "App\\Shared\\RemoteWorker" in tokens


def test_unused_import_respects_phpdoc_type_usage(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/PromptBuilder.php",
        "<?php\nuse App\\DTO\\File;\n\n/**\n * @method File generate()\n */\nclass PromptBuilder {}\n",
    )
    file_id = store.insert_file(
        FileInfo(path="app/PromptBuilder.php", language=Language.PHP, size=0)
    )
    store.insert_dependency(
        DependencyInfo(
            source_file_id=file_id,
            target_name="App\\DTO\\File",
            type=DependencyType.IMPORT,
            line=2,
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.unused_imports == []
    store.close()


def test_private_method_callback_reference_is_not_flagged(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/CallbackExample.php",
        "<?php\nclass CallbackExample {\n    private function sort_items($a, $b) { return 0; }\n    public function run() { uasort($items, array($this, 'sort_items')); }\n}\n",
    )
    file_id = store.insert_file(
        FileInfo(path="app/CallbackExample.php", language=Language.PHP, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=file_id,
            type=SymbolType.METHOD,
            name="sort_items",
            visibility=Visibility.PRIVATE,
            line_start=3,
            line_end=3,
            metadata={},
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.unused_methods == []
    store.close()


def test_abandoned_class_is_rescued_by_string_class_reference(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/bootstrap.php",
        "<?php\n$className = 'RuntimeLoadedClass';\n",
    )
    store.insert_file(FileInfo(path="app/bootstrap.php", language=Language.PHP, size=0))

    _write(
        project_root,
        "app/RuntimeLoadedClass.php",
        "<?php\nclass RuntimeLoadedClass {}\n",
    )
    file_id = store.insert_file(
        FileInfo(path="app/RuntimeLoadedClass.php", language=Language.PHP, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=file_id,
            type=SymbolType.CLASS,
            name="RuntimeLoadedClass",
            line_start=2,
            line_end=2,
            metadata={},
        )
    )

    findings = find_abandoned_classes(store, allowed_languages=["php"])

    assert findings == []
    store.close()


def test_abandoned_class_is_rescued_by_register_dependency(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/bootstrap.php",
        "<?php\nEvent::listen(UserRegistered::class, App\\\\Listeners\\\\SendWelcomeEmail::class);\n",
    )
    bootstrap_file_id = store.insert_file(
        FileInfo(path="app/bootstrap.php", language=Language.PHP, size=0)
    )
    store.insert_dependency(
        DependencyInfo(
            source_file_id=bootstrap_file_id,
            target_name="App\\Listeners\\SendWelcomeEmail",
            type=DependencyType.REGISTER,
            line=2,
        )
    )

    _write(
        project_root,
        "app/Listeners/SendWelcomeEmail.php",
        "<?php\nnamespace App\\Listeners;\n\nclass SendWelcomeEmail {}\n",
    )
    listener_file_id = store.insert_file(
        FileInfo(
            path="app/Listeners/SendWelcomeEmail.php",
            language=Language.PHP,
            size=0,
        )
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=listener_file_id,
            type=SymbolType.CLASS,
            name="SendWelcomeEmail",
            namespace="App\\Listeners",
            line_start=4,
            line_end=4,
            metadata={},
        )
    )

    findings = find_abandoned_classes(store, allowed_languages=["php"])

    assert findings == []
    store.close()


def test_python_unused_imports_respect___all___reexports(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "pkg/__init__.py",
        "from .client import Client\n\n__all__ = ['Client']\n",
    )
    store.insert_file(
        FileInfo(path="pkg/__init__.py", language=Language.PYTHON, size=0)
    )

    _write(
        project_root,
        "pkg/unused.py",
        "from .client import Client\n",
    )
    store.insert_file(FileInfo(path="pkg/unused.py", language=Language.PYTHON, size=0))

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert "pkg/__init__.py" not in {
        finding.file_path for finding in result.unused_imports
    }
    assert "pkg/unused.py" in {finding.file_path for finding in result.unused_imports}
    store.close()


def test_ts_unused_imports_detect_value_and_type_bindings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/example.ts",
        "import foo, { usedThing, unusedThing as staleThing, type UsedType, type MissingType } from './deps'\n"
        "import type { SharedType, LostType as GoneType } from './types'\n"
        "import * as registry from './registry'\n"
        "import './side-effects'\n\n"
        "const value: UsedType = usedThing(foo)\n"
        "const shared: SharedType | null = null\n"
        "console.log(value, registry, shared)\n",
    )
    store.insert_file(
        FileInfo(
            path="resources/js/example.ts",
            language=Language.TYPESCRIPT,
            size=0,
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    flagged_names = {finding.name for finding in result.unused_imports}
    assert flagged_names == {"staleThing", "MissingType", "GoneType"}
    store.close()


def test_vue_unused_imports_respect_template_usage(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/Components/ChatWidget.vue",
        "<template>\n"
        "  <ChatMessage />\n"
        "  <NButton>\n"
        "    <template #icon>\n"
        '      <NIcon :component="Close" />\n'
        "    </template>\n"
        "  </NButton>\n"
        "</template>\n"
        '<script setup lang="ts">\n'
        "import ChatMessage from './ChatMessage.vue'\n"
        "import { NButton, NIcon } from 'naive-ui'\n"
        "import { Close } from '@vicons/carbon'\n"
        "import { formatLabel, unusedHelper } from './helpers'\n"
        "console.log(formatLabel('x'))\n"
        "</script>\n",
    )
    store.insert_file(
        FileInfo(
            path="resources/js/Components/ChatWidget.vue",
            language=Language.VUE,
            size=0,
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    flagged_names = {finding.name for finding in result.unused_imports}
    assert flagged_names == {"unusedHelper"}
    store.close()


def test_ts_unused_imports_respect_object_shorthand_usage(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/icons.ts",
        "import { Activity, Report, WarningAlt } from '@vicons/carbon'\n"
        "const iconMap = {\n"
        "    Activity,\n"
        "    Report,\n"
        "}\n"
        "console.log(iconMap)\n",
    )
    store.insert_file(
        FileInfo(path="resources/js/icons.ts", language=Language.TYPESCRIPT, size=0)
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    flagged_names = {finding.name for finding in result.unused_imports}
    assert flagged_names == {"WarningAlt"}
    store.close()


def test_tsx_unused_imports_respect_jsx_component_usage(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/Navbar.tsx",
        "import { Moon, Sun, List, X, Ghost } from '@phosphor-icons/react'\n"
        "export function Navbar({ isDark, isMenuOpen }: { isDark: boolean; isMenuOpen: boolean }) {\n"
        "    return (\n"
        "        <div>\n"
        "            {isDark ? <Sun /> : <Moon />}\n"
        "            {isMenuOpen ? <X /> : <List className='w-6 h-6' />}\n"
        "        </div>\n"
        "    )\n"
        "}\n",
    )
    store.insert_file(
        FileInfo(
            path="resources/js/Navbar.tsx",
            language=Language.TYPESCRIPT,
            size=0,
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    flagged_names = {finding.name for finding in result.unused_imports}
    assert flagged_names == {"Ghost"}
    store.close()


def test_ts_private_method_callback_reference_is_not_flagged(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/ClickHandler.ts",
        "class ClickHandler {\n"
        "    private handleClick(): void {}\n"
        "    register(): void {\n"
        "        window.addEventListener('click', this.handleClick)\n"
        "    }\n"
        "}\n",
    )
    file_id = store.insert_file(
        FileInfo(
            path="resources/js/ClickHandler.ts",
            language=Language.TYPESCRIPT,
            size=0,
        )
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=file_id,
            type=SymbolType.METHOD,
            name="handleClick",
            visibility=Visibility.PRIVATE,
            line_start=2,
            line_end=2,
            metadata={"class": "ClickHandler"},
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.unused_methods == []
    store.close()


def test_ts_private_hash_property_reference_is_not_flagged(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "resources/js/cache.ts",
        "class CacheStore {\n"
        "    #cache = new Map()\n"
        "    run(): number {\n"
        "        return this.#cache.size\n"
        "    }\n"
        "}\n",
    )
    file_id = store.insert_file(
        FileInfo(path="resources/js/cache.ts", language=Language.TYPESCRIPT, size=0)
    )
    store.insert_symbol(
        SymbolInfo(
            file_id=file_id,
            type=SymbolType.PROPERTY,
            name="cache",
            visibility=Visibility.PRIVATE,
            line_start=2,
            line_end=2,
            metadata={"class": "CacheStore"},
        )
    )

    result = analyze_dead_code(store, policy=DeadCodePolicy())

    assert result.unused_properties == []
    store.close()
