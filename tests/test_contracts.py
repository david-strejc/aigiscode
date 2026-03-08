from __future__ import annotations

from pathlib import Path

from codexaudit.indexer.store import IndexStore
from codexaudit.models import FileInfo, Language
from codexaudit.report.contracts import (
    ContractLookup,
    build_contract_inventory,
    merge_contract_lookup,
)


def _make_store(project_root: Path) -> IndexStore:
    store = IndexStore(project_root / ".codexaudit" / "codexaudit.db")
    store.initialize()
    return store


def _write(project_root: Path, relative_path: str, content: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_contract_inventory_extracts_routes_hooks_env_and_config(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "routes/web.php",
        "<?php\n"
        "Route::get('/health', fn () => 'ok');\n"
        "add_action('init', 'bootstrap_theme');\n"
        "config('app.name');\n"
        "env('APP_ENV');\n",
    )
    store.insert_file(FileInfo(path="routes/web.php", language=Language.PHP, size=0))

    _write(
        project_root,
        "frontend/router.ts",
        "router.get('/api/users', handler)\n"
        "const mode = process.env.NODE_ENV;\n"
        "const api = import.meta.env.VITE_API_URL;\n",
    )
    store.insert_file(
        FileInfo(path="frontend/router.ts", language=Language.TYPESCRIPT, size=0)
    )

    _write(
        project_root,
        "app/settings.py",
        "import os\nmode = os.getenv('DJANGO_ENV')\ndebug = os.environ['DEBUG']\n",
    )
    store.insert_file(
        FileInfo(path="app/settings.py", language=Language.PYTHON, size=0)
    )

    inventory = build_contract_inventory(store)

    assert inventory["summary"]["routes"] == 2
    assert inventory["summary"]["hooks"] == 1
    assert "registered_keys" not in inventory["summary"]
    assert inventory["summary"]["env_keys"] == 5
    assert inventory["summary"]["config_keys"] == 1
    assert inventory["routes"][0]["value"] == "/api/users"
    assert {item["value"] for item in inventory["routes"]} >= {
        "/health",
        "/api/users",
    }
    assert inventory["hooks"][0]["value"] == "init"
    assert {item["value"] for item in inventory["env_keys"]} >= {
        "APP_ENV",
        "NODE_ENV",
        "VITE_API_URL",
        "DJANGO_ENV",
        "DEBUG",
    }
    assert inventory["config_keys"][0]["value"] == "app.name"

    store.close()


def test_build_contract_inventory_extracts_registered_keys(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/registry.php",
        "<?php\n"
        "register_post_status('auto-draft', []);\n"
        "$app->bind('tenant.manager', TenantManager::class);\n",
    )
    store.insert_file(FileInfo(path="app/registry.php", language=Language.PHP, size=0))

    inventory = build_contract_inventory(store)

    assert inventory["summary"]["registered_keys"] == 2
    assert {item["value"] for item in inventory["registered_keys"]} >= {
        "auto-draft",
        "tenant.manager",
    }
    store.close()


def test_build_contract_inventory_extracts_symbolic_literals(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "frontend/modes.ts",
        "export type CommandItemType =\n"
        "    | 'entity'        // Navigate to entity list\n"
        "    | 'entity-filter' // Navigate to entity with saved filter\n"
        "    | 'record'\n"
        "export type ComposeMode = 'new' | 'reply' | 'reply-all' | 'forward'\n"
        "const providers = ['google', 'outlook'] as const\n"
        "type Action = (mode: 'open' | 'close') => void\n",
    )
    store.insert_file(
        FileInfo(path="frontend/modes.ts", language=Language.TYPESCRIPT, size=0)
    )

    _write(
        project_root,
        "app/theme.php",
        "<?php\nregister_nav_menus(array('menu-1' => 'Primary', 'social' => 'Social'));\n",
    )
    store.insert_file(FileInfo(path="app/theme.php", language=Language.PHP, size=0))

    inventory = build_contract_inventory(store)

    assert inventory["summary"]["symbolic_literals"] == 13
    assert {item["value"] for item in inventory["symbolic_literals"]} >= {
        "entity",
        "entity-filter",
        "record",
        "new",
        "reply",
        "reply-all",
        "forward",
        "google",
        "outlook",
        "open",
        "close",
        "menu-1",
        "social",
    }
    store.close()


def test_merge_contract_lookup_combines_plugin_values() -> None:
    base = ContractLookup(
        routes=frozenset({"/health"}), env_keys=frozenset({"APP_ENV"})
    )

    merged = merge_contract_lookup(
        base,
        {
            "routes": ["/api/users"],
            "symbolic_literals": ["reply-all"],
            "env_keys": ["DEBUG"],
            "unknown": ["ignored"],
        },
    )

    assert merged.routes == frozenset({"/health", "/api/users"})
    assert merged.symbolic_literals == frozenset({"reply-all"})
    assert merged.env_keys == frozenset({"APP_ENV", "DEBUG"})


def test_build_contract_inventory_extracts_ruby_env_keys(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "config/puma.rb",
        'threads_count = ENV.fetch("RAILS_MAX_THREADS", 3)\n'
        'plugin :solid_queue if ENV["SOLID_QUEUE_IN_PUMA"]\n',
    )
    store.insert_file(FileInfo(path="config/puma.rb", language=Language.RUBY, size=0))

    inventory = build_contract_inventory(store)

    assert {item["value"] for item in inventory["env_keys"]} == {
        "RAILS_MAX_THREADS",
        "SOLID_QUEUE_IN_PUMA",
    }
    store.close()


def test_build_contract_inventory_extracts_stimulus_identifiers_and_events(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/javascript/controllers/media_picker_controller.js",
        'export function done() { return new CustomEvent("media-picker:done") }\n',
    )
    store.insert_file(
        FileInfo(
            path="app/javascript/controllers/media_picker_controller.js",
            language=Language.JAVASCRIPT,
            size=0,
        )
    )

    _write(
        project_root,
        "app/javascript/controllers/modal_controller.js",
        'const template = `<div data-controller="modal shortcuts" '
        'data-action="keyup@document->modal#close click->shortcuts#confirm"></div>`\n',
    )
    store.insert_file(
        FileInfo(
            path="app/javascript/controllers/modal_controller.js",
            language=Language.JAVASCRIPT,
            size=0,
        )
    )

    inventory = build_contract_inventory(store)

    assert {item["value"] for item in inventory["symbolic_literals"]} >= {
        "media-picker",
        "modal",
        "shortcuts",
        "media-picker:done",
    }
    store.close()
