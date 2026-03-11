from __future__ import annotations

from pathlib import Path

from aigiscode.indexer.parser import (
    discover_files,
    discover_unsupported_source_files,
    parse_file,
)
from aigiscode.models import AigisCodeConfig, DependencyType, Language


def test_discover_unsupported_source_files_excludes_supported_languages(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (project_root / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (project_root / "nested").mkdir()
    (project_root / "nested" / "script.ts").write_text(
        "export const x = 1;\n", encoding="utf-8"
    )
    (project_root / "lib").mkdir()
    (project_root / "lib" / "engine.rb").write_text(
        "module Engine\nend\n", encoding="utf-8"
    )
    (project_root / "cmd.go").write_text("package main\n", encoding="utf-8")

    breakdown = discover_unsupported_source_files(
        AigisCodeConfig(project_path=project_root)
    )

    assert breakdown == {"go": 1}


def test_discover_files_excludes_custom_output_dir_inside_project(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (project_root / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    output_dir = project_root / "reports" / "aigiscode"
    output_dir.mkdir(parents=True)
    (output_dir / "generated.py").write_text("print('ignore')\n", encoding="utf-8")

    files = discover_files(
        AigisCodeConfig(project_path=project_root, output_dir=output_dir)
    )

    assert [path.name for path in files] == ["app.py", "main.rs"]


def test_parse_file_extracts_python_symbols_and_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "pkg" / "service.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "from .base import BaseService\n"
        "import django.http\n\n"
        "class Service(BaseService):\n"
        "    cache_key = 'x'\n\n"
        "    def run(self, request):\n"
        "        return request\n\n"
        "def helper(value: str) -> str:\n"
        "    return value\n",
        encoding="utf-8",
    )

    symbols, dependencies = parse_file(
        file_path,
        Language.PYTHON,
        project_root=project_root,
    )

    assert {symbol.name for symbol in symbols} >= {
        "Service",
        "run",
        "helper",
        "cache_key",
    }
    assert any(
        symbol.name == "Service" and symbol.namespace == "pkg.service"
        for symbol in symbols
    )
    assert any(dep.target_name == "pkg.base.BaseService" for dep in dependencies)
    assert any(dep.target_name == "django.http" for dep in dependencies)


def test_parse_file_extracts_php_runtime_loader_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "bootstrap.php"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "<?php\n"
        "require_once ABSPATH . 'wp-admin/includes/class-wp-site-health.php';\n"
        "$table = _get_list_table('WP_Plugin_Install_List_Table');\n"
        "$cb = array('WP_Internal_Pointers', 'enqueue_scripts');\n",
        encoding="utf-8",
    )

    _symbols, dependencies = parse_file(
        file_path,
        Language.PHP,
        project_root=project_root,
    )

    assert any(
        dep.type == DependencyType.LOAD
        and dep.target_name == "wp-admin/includes/class-wp-site-health.php"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPORT
        and dep.target_name == "WP_Plugin_Install_List_Table"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPORT and dep.target_name == "WP_Internal_Pointers"
        for dep in dependencies
    )


def test_parse_file_extracts_php_registration_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "bootstrap.php"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "<?php\n"
        "add_action('init', 'bootstrap_theme');\n"
        "Event::listen(UserRegistered::class, SendWelcomeEmail::class);\n"
        "Route::get('/users', [UserController::class, 'show']);\n",
        encoding="utf-8",
    )

    _symbols, dependencies = parse_file(
        file_path,
        Language.PHP,
        project_root=project_root,
    )

    register_targets = {
        dep.target_name for dep in dependencies if dep.type == DependencyType.REGISTER
    }

    assert "bootstrap_theme" in register_targets
    assert "UserRegistered" in register_targets
    assert "SendWelcomeEmail" in register_targets
    assert "UserController" in register_targets


def test_parse_file_extracts_ruby_symbols_and_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "lib" / "spina" / "plugin.rb"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        'require_relative "engine"\n'
        "module Spina\n"
        "  module Admin\n"
        "    class Plugin < BasePlugin\n"
        "      include Cms::Concern\n"
        "      extend ActiveSupport::Concern\n"
        "      def run(arg, other = nil)\n"
        "        Image.find(arg)\n"
        "        Spina::MediaFolder.new\n"
        '        ENV.fetch("API_KEY", nil)\n'
        "      end\n"
        "    end\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )

    symbols, dependencies = parse_file(
        file_path,
        Language.RUBY,
        project_root=project_root,
    )

    assert {symbol.name for symbol in symbols} >= {"Spina", "Admin", "Plugin", "run"}
    assert any(
        symbol.name == "Plugin" and symbol.namespace == "Spina::Admin"
        for symbol in symbols
    )
    assert any(
        symbol.name == "run" and symbol.namespace == "Spina::Admin::Plugin"
        for symbol in symbols
    )
    assert any(
        dep.type == DependencyType.LOAD and dep.target_name == "engine"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.INHERIT and dep.target_name == "BasePlugin"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPLEMENT and dep.target_name == "Cms::Concern"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPLEMENT
        and dep.target_name == "ActiveSupport::Concern"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPORT and dep.target_name == "Image"
        for dep in dependencies
    )
    assert any(
        dep.type == DependencyType.IMPORT and dep.target_name == "Spina::MediaFolder"
        for dep in dependencies
    )


def test_parse_file_extracts_rust_symbols_and_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "src" / "lib.rs"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "use crate::http::{Client, Server};\n"
        "mod inner;\n"
        "pub struct Service { cache: Cache }\n"
        "enum Kind { A, B }\n"
        "trait Runner { fn run(&self); }\n"
        "impl Runner for Service {}\n"
        "impl Service { pub fn run(&self) {} }\n"
        "fn helper() { let _ = Client::new(); }\n",
        encoding="utf-8",
    )

    symbols, dependencies = parse_file(
        file_path,
        Language.RUST,
        project_root=project_root,
    )

    assert {symbol.name for symbol in symbols} >= {
        "inner",
        "Service",
        "cache",
        "Kind",
        "Runner",
        "run",
        "helper",
    }
    assert any(
        symbol.name == "cache"
        and symbol.namespace == "Service"
        and symbol.visibility.value == "private"
        for symbol in symbols
    )
    assert any(
        symbol.name == "run"
        and symbol.namespace == "Service"
        and symbol.visibility.value == "public"
        for symbol in symbols
    )
    assert any(
        symbol.name == "helper" and symbol.visibility.value == "private"
        for symbol in symbols
    )
    assert any(dep.target_name == "crate::http::Client" for dep in dependencies)
    assert any(dep.target_name == "crate::http::Server" for dep in dependencies)
    assert any(dep.type == DependencyType.IMPLEMENT and dep.target_name == "Runner" for dep in dependencies)


def test_parse_file_extracts_ts_private_hash_members(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    file_path = project_root / "resources" / "js" / "cache.ts"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        "class CacheStore {\n  #cache = new Map()\n  #flush(): void {}\n}\n",
        encoding="utf-8",
    )

    symbols, _dependencies = parse_file(
        file_path,
        Language.TYPESCRIPT,
        project_root=project_root,
    )

    assert any(
        symbol.type.value == "property"
        and symbol.name == "cache"
        and symbol.visibility.value == "private"
        for symbol in symbols
    )
    assert any(
        symbol.type.value == "method"
        and symbol.name == "flush"
        and symbol.visibility.value == "private"
        for symbol in symbols
    )
