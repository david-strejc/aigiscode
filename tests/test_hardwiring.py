from __future__ import annotations

from pathlib import Path

from aigiscode.graph.hardwiring import analyze_hardwiring
from aigiscode.indexer.store import IndexStore
from aigiscode.models import FileInfo, Language
from aigiscode.policy.models import HardwiringPolicy


def _make_store(project_root: Path) -> IndexStore:
    store = IndexStore(project_root / ".aigiscode" / "aigiscode.db")
    store.initialize()
    return store


def _write(project_root: Path, relative_path: str, content: str) -> None:
    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_python_env_reads_are_reported_outside_settings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/runtime.py",
        "import os\n\nAPI_KEY = os.getenv('API_KEY')\n",
    )
    store.insert_file(FileInfo(path="app/runtime.py", language=Language.PYTHON, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert any(
        finding.file_path == "app/runtime.py" for finding in result.env_outside_config
    )
    store.close()


def test_ruby_env_reads_are_reported_outside_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/services/runtime.rb",
        'api_key = ENV.fetch("API_KEY", nil)\n',
    )
    store.insert_file(
        FileInfo(path="app/services/runtime.rb", language=Language.RUBY, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert any(
        finding.file_path == "app/services/runtime.rb"
        for finding in result.env_outside_config
    )
    store.close()


def test_rust_env_and_network_reads_are_reported_outside_config(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "src/runtime.rs",
        'let api_key = std::env::var("API_KEY").unwrap();\n'
        'if mode == "release" { let _ = "https://api.acme.test"; }\n',
    )
    store.insert_file(FileInfo(path="src/runtime.rs", language=Language.RUST, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert any(
        finding.file_path == "src/runtime.rs" for finding in result.env_outside_config
    )
    assert any(
        finding.file_path == "src/runtime.rs" and finding.value == "release"
        for finding in result.magic_strings
    )
    assert any(
        finding.file_path == "src/runtime.rs"
        and finding.value == "https://api.acme.test"
        for finding in result.hardcoded_network
    )
    store.close()


def test_rust_build_scripts_are_treated_as_tooling_for_env_reads(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "build.rs",
        'let target = std::env::var("TARGET").unwrap();\n',
    )
    store.insert_file(FileInfo(path="build.rs", language=Language.RUST, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.env_outside_config == []
    store.close()


def test_semver_literals_do_not_become_magic_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/version_check.rb",
        'if RUBY_VERSION == "3.0.0" && RUBY_PATCHLEVEL == 0\n  true\nend\n',
    )
    store.insert_file(
        FileInfo(path="app/version_check.rb", language=Language.RUBY, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "3.0.0" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_style_contract_literals_do_not_become_magic_or_repeated_literals(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/first.js",
        'if (attr === "data-controller") { return "opacity-50" }\n'
        'element.classList.add("opacity-50")\n',
    )
    store.insert_file(
        FileInfo(path="app/first.js", language=Language.JAVASCRIPT, size=0)
    )

    _write(
        project_root,
        "app/second.js",
        'element.classList.remove("opacity-50")\n',
    )
    store.insert_file(
        FileInfo(path="app/second.js", language=Language.JAVASCRIPT, size=0)
    )

    result = analyze_hardwiring(
        store,
        min_occurrences=2,
        policy=HardwiringPolicy(repeated_literal_min_occurrences=2),
    )

    assert {finding.value for finding in result.magic_strings}.isdisjoint(
        {"data-controller", "opacity-50"}
    )
    assert "opacity-50" not in {finding.value for finding in result.repeated_literals}
    store.close()


def test_stimulus_controller_identifiers_do_not_become_magic_strings(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/javascript/controllers/media_picker_controller.js",
        "export default class {}\n",
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
        "app/controllers/images_controller.rb",
        'if params[:origin] == "media-picker"\n  true\nend\n',
    )
    store.insert_file(
        FileInfo(
            path="app/controllers/images_controller.rb",
            language=Language.RUBY,
            size=0,
        )
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "media-picker" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_hardwiring_skips_test_fixtures_and_selectors(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "tests/fixtures/dom.js",
        "if ( '#categories-all' === target ) { console.log(target); }\n",
    )
    store.insert_file(
        FileInfo(path="tests/fixtures/dom.js", language=Language.JAVASCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.total == 0
    store.close()


def test_hardwiring_skips_tooling_task_labels(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "Gruntfile.js",
        "if ( 'watch:phpunit' === grunt.cli.tasks[0] ) { console.log('x'); }\n",
    )
    store.insert_file(
        FileInfo(path="Gruntfile.js", language=Language.JAVASCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.magic_strings == []
    store.close()


def test_env_reads_in_tooling_files_are_not_reported(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "tools/build.js",
        "const flag = process.env.SOURCEMAP;\n",
    )
    store.insert_file(
        FileInfo(path="tools/build.js", language=Language.JAVASCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.env_outside_config == []
    store.close()


def test_env_reads_in_bootstrap_config_paths_are_not_reported(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "django/conf/__init__.py",
        "import os\nSETTINGS = os.environ['DJANGO_SETTINGS_MODULE']\n",
    )
    store.insert_file(
        FileInfo(path="django/conf/__init__.py", language=Language.PYTHON, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.env_outside_config == []
    store.close()


def test_cli_env_reads_without_default_are_not_reported(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/Console/Commands/seed.php",
        "<?php\n$value = env('APP_ENV');\n",
    )
    store.insert_file(
        FileInfo(path="app/Console/Commands/seed.php", language=Language.PHP, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.env_outside_config == []
    store.close()


def test_cli_env_reads_with_default_still_report(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/Console/Commands/seed.php",
        "<?php\n$value = env('APP_ENV', 'local');\n",
    )
    store.insert_file(
        FileInfo(path="app/Console/Commands/seed.php", language=Language.PHP, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert [finding.value for finding in result.env_outside_config] == ["env()"]
    store.close()


def test_svg_decimal_fragments_are_not_reported_as_ip_addresses(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/about.php",
        "<?php\n$path = 'M1.937.86.54 10 20';\n",
    )
    store.insert_file(FileInfo(path="app/about.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.hardcoded_network == []
    store.close()


def test_declared_hooks_do_not_become_repeated_literals(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/first.php",
        "<?php\nadd_action('init', 'boot_first');\ndo_action('init');\n",
    )
    store.insert_file(FileInfo(path="app/first.php", language=Language.PHP, size=0))

    _write(
        project_root,
        "app/second.php",
        "<?php\nadd_action('init', 'boot_second');\napply_filters('init', $value);\n",
    )
    store.insert_file(FileInfo(path="app/second.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(
        store,
        min_occurrences=2,
        policy=HardwiringPolicy(repeated_literal_min_occurrences=2),
    )

    assert "init" not in {finding.value for finding in result.repeated_literals}
    store.close()


def test_declared_config_keys_do_not_become_repeated_literals(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/first.php",
        "<?php\n$value = config('app.name');\n",
    )
    store.insert_file(FileInfo(path="app/first.php", language=Language.PHP, size=0))

    _write(
        project_root,
        "app/second.php",
        "<?php\n$value = config('app.name');\n",
    )
    store.insert_file(FileInfo(path="app/second.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(
        store,
        min_occurrences=2,
        policy=HardwiringPolicy(repeated_literal_min_occurrences=2),
    )

    assert "app.name" not in {finding.value for finding in result.repeated_literals}
    store.close()


def test_declared_hook_does_not_become_magic_string(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/hooks.php",
        "<?php\nadd_action('init', 'boot_theme');\nif ($hook === 'init') { return; }\n",
    )
    store.insert_file(FileInfo(path="app/hooks.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "init" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_protocol_literals_do_not_become_magic_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/protocols.php",
        "<?php\n"
        "if ($type === 'image/webp') { return; }\n"
        "if ($token === '+HTML') { return; }\n"
        "if ($bom === '\\\\xFE\\\\xFF') { return; }\n",
    )
    store.insert_file(FileInfo(path="app/protocols.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert {finding.value for finding in result.magic_strings}.isdisjoint(
        {"image/webp", "+HTML", "\\xFE\\xFF"}
    )
    store.close()


def test_public_provider_default_urls_are_not_reported_as_hardcoded_network(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/models/avatar.rb",
        '<?php\n$avatar = "https://www.gravatar.com/avatar/{$hash}?d={$fallback}";\n'
        '$fallback = "https://eu.ui-avatars.com/api/{$name}/128";\n',
    )
    store.insert_file(
        FileInfo(path="app/models/avatar.rb", language=Language.PHP, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.hardcoded_network == []
    store.close()


def test_registered_key_does_not_become_magic_string(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/status.php",
        "<?php\nregister_post_status('auto-draft', []);\nif ($status === 'auto-draft') { return; }\n",
    )
    store.insert_file(FileInfo(path="app/status.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "auto-draft" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_protocol_markers_do_not_become_magic_strings_in_context(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/http.php",
        "<?php\n"
        "if ($header === 'content-type') { return; }\n"
        "if ($charset === 'ISO-8859-1') { return; }\n"
        "if ($operator === 'NOT IN') { return; }\n"
        "if ($url === 'http://') { return; }\n",
    )
    store.insert_file(FileInfo(path="app/http.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert {finding.value for finding in result.magic_strings}.isdisjoint(
        {"content-type", "ISO-8859-1", "NOT IN", "http://"}
    )
    store.close()


def test_setting_names_do_not_become_magic_strings_in_settings_context(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "django/conf/runtime.py",
        "if settings_module == 'SECRET_KEY':\n    raise RuntimeError('bad')\n",
    )
    store.insert_file(
        FileInfo(path="django/conf/runtime.py", language=Language.PYTHON, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "SECRET_KEY" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_header_env_names_do_not_become_magic_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/asgi.py",
        "if corrected_name == 'HTTP_COOKIE':\n    return headers['HTTP_COOKIE']\n",
    )
    store.insert_file(FileInfo(path="app/asgi.py", language=Language.PYTHON, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "HTTP_COOKIE" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_module_paths_do_not_become_magic_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/commands.py",
        "if app == 'django.core':\n    return module == 'psycopg2._range'\n",
    )
    store.insert_file(
        FileInfo(path="app/commands.py", language=Language.PYTHON, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert {finding.value for finding in result.magic_strings}.isdisjoint(
        {"django.core", "psycopg2._range"}
    )
    store.close()


def test_page_slug_markers_do_not_become_magic_strings_in_page_context(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/pages.php",
        "<?php\nif ( 'plugin-install' === $pagenow ) { return; }\n",
    )
    store.insert_file(FileInfo(path="app/pages.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "plugin-install" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_documentation_urls_are_not_reported_as_hardcoded_network(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/help.php",
        "<?php\n"
        "__('<a href=\"https://docs.example.com/guide\">Docs</a>');\n"
        "$schema = 'http://purl.org/dc/elements/1.1/';\n",
    )
    store.insert_file(FileInfo(path="app/help.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.hardcoded_network == []
    store.close()


def test_runtime_api_urls_still_report_as_hardcoded_network(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/api.php",
        "<?php\n$endpoint = 'https://api.acme.local/v1/users';\n",
    )
    store.insert_file(FileInfo(path="app/api.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert [finding.value for finding in result.hardcoded_network] == [
        "https://api.acme.local/v1/users"
    ]
    assert result.hardcoded_network[0].confidence == "high"
    store.close()


def test_declared_symbolic_literals_do_not_become_magic_strings(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "frontend/compose.ts",
        "export type ComposeMode = 'new' | 'reply' | 'reply-all' | 'forward'\n"
        "if (mode === 'reply-all') { return true }\n",
    )
    store.insert_file(
        FileInfo(path="frontend/compose.ts", language=Language.TYPESCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "reply-all" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_multiline_symbolic_literals_do_not_become_magic_strings(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "frontend/commands.ts",
        "export type CommandItemType =\n"
        "  | 'entity'\n"
        "  | 'entity-filter'\n"
        "  | 'record'\n"
        "if (item.type === 'entity-filter') { return true }\n",
    )
    store.insert_file(
        FileInfo(path="frontend/commands.ts", language=Language.TYPESCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "entity-filter" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_registered_array_keys_do_not_become_magic_strings(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/theme.php",
        "<?php\n"
        "register_nav_menus(array('menu-1' => 'Primary'));\n"
        "if ( 'menu-1' === $args->theme_location ) { return; }\n",
    )
    store.insert_file(FileInfo(path="app/theme.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert "menu-1" not in {finding.value for finding in result.magic_strings}
    store.close()


def test_ui_action_ids_do_not_become_magic_strings_in_interactive_paths(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "src/js/admin/comments.js",
        "if ( 'edit-comment' === this.act ) { return true; }\n"
        "if ( 'bulk-edit' === id ) { return true; }\n",
    )
    store.insert_file(
        FileInfo(path="src/js/admin/comments.js", language=Language.JAVASCRIPT, size=0)
    )

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert {finding.value for finding in result.magic_strings}.isdisjoint(
        {"edit-comment", "bulk-edit"}
    )
    store.close()


def test_identifier_urls_are_not_reported_as_hardcoded_network(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/meta.php",
        "<?php\n"
        "$links['https://api.w.org/theme-file'] = $resolved;\n"
        'echo \'<link rel="profile" href="https://gmpg.org/xfn/11" />\';\n',
    )
    store.insert_file(FileInfo(path="app/meta.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert result.hardcoded_network == []
    store.close()


def test_provider_registry_urls_are_low_confidence_network(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/providers.php",
        "<?php\n"
        "$providers = array('#x#' => array('https://publish.twitter.com/oembed', true));\n",
    )
    store.insert_file(FileInfo(path="app/providers.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(store, policy=HardwiringPolicy())

    assert [finding.value for finding in result.hardcoded_network] == [
        "https://publish.twitter.com/oembed"
    ]
    assert result.hardcoded_network[0].confidence == "low"
    store.close()


def test_protocol_literals_do_not_become_repeated_literals(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = _make_store(project_root)

    _write(
        project_root,
        "app/first.php",
        "<?php\n$charset = 'utf-8';\n$memory = ':memory:';\n",
    )
    store.insert_file(FileInfo(path="app/first.php", language=Language.PHP, size=0))

    _write(
        project_root,
        "app/second.php",
        "<?php\n$charset = 'utf-8';\n$memory = ':memory:';\n",
    )
    store.insert_file(FileInfo(path="app/second.php", language=Language.PHP, size=0))

    result = analyze_hardwiring(
        store,
        min_occurrences=2,
        policy=HardwiringPolicy(repeated_literal_min_occurrences=2),
    )

    assert {finding.value for finding in result.repeated_literals}.isdisjoint(
        {"utf-8", ":memory:"}
    )
    store.close()
