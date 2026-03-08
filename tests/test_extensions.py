from __future__ import annotations

from pathlib import Path

import networkx as nx

from codexaudit.builtin_runtime_plugins import load_builtin_runtime_plugins
from codexaudit.contracts import ContractLookup
from codexaudit.extensions import (
    apply_contract_lookup_plugins,
    apply_graph_result_plugins,
    apply_hardwiring_finding_plugins,
    build_report_extensions,
    load_external_plugins,
)
from codexaudit.graph.hardwiring import HardwiringFinding, analyze_hardwiring
from codexaudit.indexer.store import IndexStore
from codexaudit.models import FileInfo, GraphAnalysisResult, Language, ReportData
from codexaudit.policy.models import HardwiringPolicy
from codexaudit.policy.plugins import resolve_policy
from codexaudit.report.generator import generate_json_report, generate_markdown_report


def test_external_plugin_hooks_extend_policy_and_report(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plugin_path = tmp_path / "demo_plugin.py"
    plugin_path.write_text(
        """
PLUGIN_NAME = "demo"

def build_policy_patch(project_path, selected_plugins):
    return {"graph": {"orphan_entry_patterns": ["**/entry.php"]}}

def refine_graph_result(graph_result, **kwargs):
    graph_result.orphan_files = [
        path for path in graph_result.orphan_files if not path.endswith("entry.php")
    ]
    return graph_result

def build_report_extensions(report, **kwargs):
    return {"remaining_orphans": len(report.graph_analysis.orphan_files)}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    plugins = load_external_plugins([str(plugin_path)])
    policy = resolve_policy(
        project_root,
        plugin_modules=[str(plugin_path)],
        external_plugins=plugins,
    )
    graph_result = apply_graph_result_plugins(
        GraphAnalysisResult(orphan_files=["foo/entry.php", "foo/worker.php"]),
        plugins,
        graph=nx.DiGraph(),
        store=None,
        project_path=project_root,
        policy=policy,
    )

    report = ReportData(
        project_path=str(project_root),
        graph_analysis=graph_result,
    )
    report.extensions = build_report_extensions(
        plugins,
        report=report,
        graph=nx.DiGraph(),
        store=None,
        project_path=project_root,
        policy=policy,
    )

    assert "**/entry.php" in policy.graph.orphan_entry_patterns
    assert graph_result.orphan_files == ["foo/worker.php"]
    assert report.extensions == {"demo": {"remaining_orphans": 1}}
    assert generate_json_report(report)["extensions"] == {
        "demo": {"remaining_orphans": 1}
    }
    assert "## Extensions" in generate_markdown_report(report)


def test_external_plugin_hooks_refine_contracts_and_hardwiring(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plugin_path = tmp_path / "hardwiring_plugin.py"
    plugin_path.write_text(
        """
PLUGIN_NAME = "hardwiring_demo"

def refine_contract_lookup(contract_lookup, **kwargs):
    return {"symbolic_literals": ["reply-all"]}

def refine_hardwiring_findings(findings, category, **kwargs):
    if category != "hardcoded_ip_url":
        return findings
    return [f for f in findings if "provider.example.com" not in f.value]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    (project_root / ".codexaudit").mkdir()
    (project_root / "frontend").mkdir()
    (project_root / "app").mkdir()
    (project_root / "frontend" / "compose.ts").write_text(
        "if (mode === 'reply-all') { return true }\n",
        encoding="utf-8",
    )
    (project_root / "app" / "service.php").write_text(
        "<?php\n$endpoint = 'https://provider.example.com/token';\n",
        encoding="utf-8",
    )

    store = IndexStore(project_root / ".codexaudit" / "codexaudit.db")
    store.initialize()
    store.insert_file(
        FileInfo(path="frontend/compose.ts", language=Language.TYPESCRIPT, size=0)
    )
    store.insert_file(FileInfo(path="app/service.php", language=Language.PHP, size=0))

    plugins = load_external_plugins([str(plugin_path)])
    policy = resolve_policy(
        project_root,
        plugin_modules=[str(plugin_path)],
        external_plugins=plugins,
    )

    lookup = apply_contract_lookup_plugins(
        ContractLookup(),
        plugins,
        store=store,
        project_path=project_root,
        policy=policy,
    )
    assert "reply-all" in lookup.symbolic_literals

    network_finding = HardwiringFinding(
        file_path="app/service.php",
        line=2,
        category="hardcoded_ip_url",
        value="https://provider.example.com/token",
        context="$endpoint = 'https://provider.example.com/token';",
        severity="high",
        confidence="high",
        suggestion="Move URL to config.",
    )
    refined_network = apply_hardwiring_finding_plugins(
        [network_finding],
        plugins,
        category="hardcoded_ip_url",
        store=store,
        project_path=project_root,
        policy=policy,
        contract_lookup=lookup,
    )
    assert refined_network == []

    result = analyze_hardwiring(
        store,
        policy=HardwiringPolicy(),
        external_plugins=plugins,
        project_path=project_root,
    )

    assert "reply-all" not in {f.value for f in result.magic_strings}
    assert result.hardcoded_network == []
    store.close()


def test_builtin_runtime_plugins_load_for_named_profiles() -> None:
    plugins = load_builtin_runtime_plugins(["generic", "django", "wordpress"])

    assert [plugin.name for plugin in plugins] == [
        "django-runtime",
        "wordpress-runtime",
    ]


def test_resolve_policy_auto_detects_framework_profiles(
    tmp_path: Path,
) -> None:
    rails_root = tmp_path / "rails_engine"
    rails_root.mkdir()
    (rails_root / "spina.gemspec").write_text("", encoding="utf-8")
    (rails_root / "lib" / "spina").mkdir(parents=True)
    (rails_root / "lib" / "spina" / "engine.rb").write_text("", encoding="utf-8")

    django_root = tmp_path / "django_project"
    django_root.mkdir()
    (django_root / "manage.py").write_text("", encoding="utf-8")

    wordpress_root = tmp_path / "wordpress_project"
    wordpress_root.mkdir()
    (wordpress_root / "wp-admin").mkdir()
    (wordpress_root / "wp-includes").mkdir()

    rails_policy = resolve_policy(rails_root)
    django_policy = resolve_policy(django_root)
    wordpress_policy = resolve_policy(wordpress_root)

    assert "rails" in rails_policy.plugins_applied
    assert "django" in django_policy.plugins_applied
    assert "wordpress" in wordpress_policy.plugins_applied

    django_source_root = tmp_path / "django_source"
    (django_source_root / "django").mkdir(parents=True)
    (django_source_root / "django" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )

    wordpress_source_root = tmp_path / "wordpress_source"
    (wordpress_source_root / "src" / "wp-admin").mkdir(parents=True)
    (wordpress_source_root / "src" / "wp-includes").mkdir(parents=True)

    django_source_policy = resolve_policy(django_source_root)
    wordpress_source_policy = resolve_policy(wordpress_source_root)

    assert "django" in django_source_policy.plugins_applied
    assert "wordpress" in wordpress_source_policy.plugins_applied


def test_builtin_runtime_plugins_refine_framework_specific_hardwiring(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".codexaudit").mkdir()
    (project_root / "django" / "core" / "management").mkdir(parents=True)
    (project_root / "src" / "js" / "_enqueues" / "wp").mkdir(parents=True)
    (project_root / "src" / "wp-admin").mkdir(parents=True)

    (project_root / "django" / "core" / "management" / "color.py").write_text(
        "import os\nstyle = os.environ.get('DJANGO_COLORS', '')\n",
        encoding="utf-8",
    )
    (project_root / "src" / "js" / "_enqueues" / "wp" / "updates.js").write_text(
        "if ( 'update-plugin' === action ) { return true; }\n",
        encoding="utf-8",
    )
    (project_root / "src" / "wp-admin" / "about.php").write_text(
        "<?php\n__('https://wordpress.org/about/');\n",
        encoding="utf-8",
    )

    store = IndexStore(project_root / ".codexaudit" / "codexaudit.db")
    store.initialize()
    store.insert_file(
        FileInfo(
            path="django/core/management/color.py",
            language=Language.PYTHON,
            size=0,
        )
    )
    store.insert_file(
        FileInfo(
            path="src/js/_enqueues/wp/updates.js",
            language=Language.JAVASCRIPT,
            size=0,
        )
    )
    store.insert_file(
        FileInfo(path="src/wp-admin/about.php", language=Language.PHP, size=0)
    )

    runtime_plugins = load_builtin_runtime_plugins(["django", "wordpress"])
    result = analyze_hardwiring(
        store,
        policy=HardwiringPolicy(),
        external_plugins=runtime_plugins,
        project_path=project_root,
    )

    assert result.env_outside_config == []
    assert "update-plugin" not in {f.value for f in result.magic_strings}
    assert result.hardcoded_network == []
    store.close()
