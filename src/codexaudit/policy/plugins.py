"""Built-in plugins and plugin loading for codexaudit."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from codexaudit.extensions import (
    ExternalPlugin,
    build_policy_patch_from_plugin,
    load_external_plugins,
)
from codexaudit.models import ArchitecturalLayer
from codexaudit.policy.models import AnalysisPolicy

logger = logging.getLogger(__name__)


BUILTIN_PLUGINS: dict[str, dict[str, Any]] = {
    "generic": {
        "description": "Safe defaults for mixed-language monorepos.",
        "graph": {
            "js_fuzzy_import_resolution": False,
            "js_import_aliases": {},
            "layer_patterns": {
                "types.ts": "Utility",
                "types.d.ts": "Utility",
            },
            "layer_violation_excludes": [],
            "orphan_entry_patterns": [
                "**/*.hook.php",
                "**/*.hooks.php",
                "**/module.php",
                "**/Module.php",
                "**/manifest.php",
                "**/Manifest.php",
            ],
        },
        "dead_code": {
            "attribute_usage_names": ["Override"],
            "abandoned_languages": ["php"],
            "abandoned_entry_patterns": [
                "**/*.hook.php",
                "**/*.hooks.php",
                "**/module.php",
                "**/Module.php",
                "**/manifest.php",
                "**/Manifest.php",
            ],
            "abandoned_dynamic_reference_patterns": [
                "**/*.hook.php",
                "**/*.hooks.php",
                "**/module.php",
                "**/Module.php",
                "**/manifest.php",
                "**/Manifest.php",
                "**/bootstrap/**/*.php",
                "**/config/**/*.php",
                "**/config/*.php",
                "**/routes/**/*.php",
                "**/*ServiceProvider.php",
            ],
        },
        "hardwiring": {
            "entity_context_require_regexes": [
                r"\b(?:input|query)\(\s*['\"]entityTypes?['\"]\s*,",
            ],
            "entity_context_allow_regexes": [],
            "low_signal_literals": [
                "all",
                "none",
                "error",
                "warning",
                "success",
                "pending",
                "completed",
                "active",
                "paused",
                "default",
                "create",
                "edit",
                "select",
                "count",
                "page",
                "group",
                "list",
                "team",
                "user",
                "admin",
                "main",
                "center",
                "dark",
                "light",
                "global",
                "custom",
                "action",
                "stream",
                "record",
                "tool",
                "tabs",
                "sidebar",
                "online",
                "offline",
                "assistant",
                "draft",
                "failed",
                "skipped",
                "auto",
                "link",
                "entity",
                "ok",
            ],
            "magic_string_min_length": 5,
            "magic_string_skip_path_patterns": ["**/Console/**"],
            "magic_string_signal_context_regexes": [
                r"\$(?:mode|status|state|scope|provider|backend|algorithm|phase|event|action|operation|sourceType|viewType|requestedView|columnId)\b",
                r"\b(?:switch|match)\s*\([^)]*\$(?:mode|status|state|scope|provider|backend|algorithm|phase|event|action|operation|sourceType|viewType|requestedView|columnId)\b",
            ],
            "magic_string_noise_context_regexes": [
                r"\[(?:'|\")(?:options|relation|type|storage|panel|key)(?:'|\")\]",
                r"\$(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|column(?:name)?|key|normalizedField)\b\s*(?:===|!==|==|!=)",
                r"(?:===|!==|==|!=)\s*\$(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|column(?:name)?|key|normalizedField)\b",
            ],
            "js_env_allow_names": [
                "DEV",
                "PROD",
                "MODE",
                "SSR",
                "BASE_URL",
                "NODE_ENV",
            ],
            "repeated_literal_min_occurrences": 3,
            "repeated_literal_min_length": 5,
            "repeated_literal_min_distinct_dirs": 2,
            "repeated_literal_require_compound": True,
            "repeated_literal_skip_regexes": [
                r"^\s*[:@]",
                r"^\s*v-[a-z][a-z0-9-]*=?\s*$",
                r"=\s*$",
                r"^\s*(?:\.\.?/|~?@/)",
                r"^\s*--[a-z0-9-]+(?:=.*)?\s*$",
                r"^\s*-\d+\s+[A-Za-z]+\s*$",
                r"^[A-Za-z]{1,8}/[A-Za-z]{1,8}$",
            ],
        },
        "ai": {
            "primary_backend": "codex_sdk",
            "allow_codex_cli_fallback": True,
            "allow_claude_fallback": True,
            "codex_model": "gpt-5.3-codex",
            "synthesis_model": "gpt-5.3-codex",
            "review_model": "gpt-5.3-codex",
        },
    },
    "laravel": {
        "description": "Laravel-aware suppressions and entry-point handling.",
        "graph": {
            "layer_violation_excludes": [
                "config/*.php",
                "app/Modules/*/config/*.php",
            ],
            "orphan_entry_patterns": [
                "**/*.hooks.php",
                "**/*.actions.php",
                "**/*ServiceProvider.php",
                "resources/js/app.ts",
            ],
        },
        "dead_code": {
            "attribute_usage_names": ["Override", "EntityAttr"],
            "abandoned_languages": ["php"],
            "abandoned_entry_patterns": [
                "/Providers/",
                "/Console/",
                "/database/seeders/",
                "/database/factories/",
                "**/*ServiceProvider.php",
            ],
            "abandoned_dynamic_reference_patterns": [
                "**/bootstrap/app.php",
                "**/bootstrap/providers.php",
                "**/config/**/*.php",
                "**/config/*.php",
                "**/routes/**/*.php",
                "**/*ServiceProvider.php",
            ],
        },
        "hardwiring": {
            "entity_context_require_regexes": [
                r"\b(?:input|query)\(\s*['\"]entityTypes?['\"]\s*,",
            ],
            "entity_context_allow_regexes": [
                r"['\"]entity(Type)?['\"]\s*=>",
                r"\blookup(Id)?\s*\(",
            ],
            "magic_string_skip_path_patterns": [
                "**/Console/Commands/**",
            ],
            "magic_string_noise_context_regexes": [
                r"\b(?:switch|match)\s*\([^)]*\$(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|column(?:name)?|key)\b",
            ],
        },
    },
    "newerp": {
        "description": "NewERP profile tuned for app/ and resources/js architecture.",
        "graph": {
            "js_import_aliases": {
                "@/": "resources/js/",
                "~@/": "resources/js/",
            },
            "layer_violation_excludes": [
                "resources/js/**",
                "app/Modules/*/resources/js/**",
            ],
            "orphan_entry_patterns": [
                "app/Actions/**/*.php",
            ],
        },
        "dead_code": {
            "attribute_usage_names": ["Override", "EntityAttr"],
            "abandoned_languages": ["php"],
            "abandoned_entry_patterns": [
                "/Contracts/",
                "/resources/js/__tests__/",
                "/Resources/web-addons/",
            ],
            "abandoned_dynamic_reference_patterns": [
                "**/*.hooks.php",
                "**/config/**/*.php",
                "**/routes/**/*.php",
            ],
        },
        "hardwiring": {
            "entity_context_require_regexes": [
                r"\b(?:input|query)\(\s*['\"]entityTypes?['\"]\s*,",
                r"\bEntityRegistry::(?:get|getClass)\s*\(",
                r"\bFieldLoader::load\s*\(",
                r"\bcan(?:Read|Write|Update|Delete|Access)\s*\(",
                r"\bgetForParent\s*\(",
            ],
            "entity_context_allow_regexes": [
                r"['\"]entity(Type)?['\"]\s*=>",
                r"\blookup(Id)?\s*\(",
                r"\b(parent|related|target)_type\b",
                r"\btypeMap\b",
                r"\bespoRaw(Type|TargetType)\b",
            ],
            "magic_string_skip_path_patterns": [
                "**/resources/js/Components/**",
                "**/resources/js/Pages/**",
            ],
            "magic_string_signal_context_regexes": [
                r"\$(?:mode|status|scope|provider|backend|algorithm|phase|event|action|operation|viewType|requestedView|sourceType|columnId)\b",
                r"\b(?:switch|match)\s*\([^)]*\$(?:mode|status|scope|provider|backend|algorithm|phase|event|action|operation|viewType|requestedView|sourceType|columnId)\b",
            ],
            "magic_string_noise_context_regexes": [
                r"\b(?:switch|match)\s*\([^)]*\$(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|column(?:name)?|key)\b",
                r"\$field\[['\"]options['\"]\]\s*(?:===|!==|==|!=)",
            ],
        },
        "ai": {
            "primary_backend": "codex_sdk",
            "allow_codex_cli_fallback": True,
            "allow_claude_fallback": True,
        },
    },
    "rails": {
        "description": "Rails-aware runtime entry and scaffold handling.",
        "graph": {
            "layer_violation_excludes": [
                "app/components/**/*.rb",
                "app/helpers/**/*.rb",
                "test/dummy/app/helpers/**/*.rb",
                "spec/dummy/app/helpers/**/*.rb",
            ],
            "orphan_entry_patterns": [
                "config/routes.rb",
                "config/application.rb",
                "config/environment.rb",
                "config.ru",
                "config/environments/**/*.rb",
                "config/initializers/**/*.rb",
                "db/seeds.rb",
                "db/migrate/*.rb",
                "db/migrate/**/*.rb",
                "app/controllers/**/*.rb",
                "app/jobs/**/*.rb",
                "app/mailers/**/*.rb",
                "app/components/**/*.rb",
                "app/presenters/**/*.rb",
                "lib/generators/*.rb",
                "lib/generators/**/*.rb",
                "lib/**/*engine.rb",
                "lib/**/*railtie.rb",
                "test/mailers/previews/*.rb",
                "test/mailers/previews/**/*.rb",
                "spec/mailers/previews/*.rb",
                "spec/mailers/previews/**/*.rb",
                "test/dummy/config/**/*.rb",
                "spec/dummy/config/**/*.rb",
            ],
        },
        "hardwiring": {
            "skip_path_patterns": [
                "lib/generators/**/templates/**",
                "test/dummy/**",
                "spec/dummy/**",
            ],
        },
    },
    "django": {
        "description": "Django-aware runtime plugin profile for framework conventions.",
    },
    "wordpress": {
        "description": "WordPress-aware runtime plugin profile for admin/runtime conventions.",
    },
}

_LAYER_NAME_MAP = {layer.value.lower(): layer.value for layer in ArchitecturalLayer}


def list_plugins() -> dict[str, str]:
    """Return available plugin names and short descriptions."""
    result: dict[str, str] = {}
    for name, payload in BUILTIN_PLUGINS.items():
        result[name] = str(payload.get("description", ""))
    return result


def _list_item_key(item: Any) -> str:
    if isinstance(item, (dict, list)):
        try:
            return json.dumps(item, sort_keys=True, ensure_ascii=False)
        except TypeError:
            return repr(item)
    return repr(item)


def _merge_unique_list(base: list[Any], patch: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*base, *patch]:
        key = _list_item_key(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = _merge_unique_list(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_json_policy(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed
        logger.warning("Policy file %s must contain a JSON object", path)
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load policy file %s: %s", path, exc)
        return {}


def _normalize_layer_name(name: str) -> str | None:
    return _LAYER_NAME_MAP.get(name.strip().lower())


def _normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(policy)

    plugins_applied = normalized.get("plugins_applied")
    if isinstance(plugins_applied, list):
        normalized["plugins_applied"] = _merge_unique_list([], plugins_applied)

    graph = normalized.get("graph")
    if isinstance(graph, dict):
        graph = dict(graph)
        layer_patterns = graph.get("layer_patterns")
        if isinstance(layer_patterns, dict):
            cleaned_patterns: dict[str, str] = {}
            for pattern, layer_name in layer_patterns.items():
                if not isinstance(pattern, str) or not isinstance(layer_name, str):
                    continue
                normalized_layer = _normalize_layer_name(layer_name)
                if normalized_layer is None:
                    logger.warning(
                        "Ignoring unsupported layer name '%s' for pattern '%s'",
                        layer_name,
                        pattern,
                    )
                    continue
                cleaned_patterns[pattern] = normalized_layer
            graph["layer_patterns"] = cleaned_patterns

        for list_key in ("layer_violation_excludes", "orphan_entry_patterns"):
            values = graph.get(list_key)
            if isinstance(values, list):
                graph[list_key] = _merge_unique_list([], values)
        normalized["graph"] = graph

    dead_code = normalized.get("dead_code")
    if isinstance(dead_code, dict):
        dead_code = dict(dead_code)
        for list_key in (
            "attribute_usage_names",
            "abandoned_languages",
            "abandoned_entry_patterns",
            "abandoned_dynamic_reference_patterns",
        ):
            values = dead_code.get(list_key)
            if isinstance(values, list):
                dead_code[list_key] = _merge_unique_list([], values)
        normalized["dead_code"] = dead_code

    hardwiring = normalized.get("hardwiring")
    if isinstance(hardwiring, dict):
        hardwiring = dict(hardwiring)
        for list_key in (
            "entity_context_require_regexes",
            "entity_context_allow_regexes",
            "low_signal_literals",
            "magic_string_skip_path_patterns",
            "magic_string_signal_context_regexes",
            "magic_string_noise_context_regexes",
            "repeated_literal_skip_regexes",
            "skip_path_patterns",
            "js_env_allow_names",
        ):
            values = hardwiring.get(list_key)
            if isinstance(values, list):
                hardwiring[list_key] = _merge_unique_list([], values)
        normalized["hardwiring"] = hardwiring

    return normalized


def resolve_policy(
    project_path: Path,
    plugin_names: list[str] | None = None,
    policy_file: Path | None = None,
    plugin_modules: list[str] | None = None,
    external_plugins: list[ExternalPlugin] | None = None,
) -> AnalysisPolicy:
    """Resolve final analysis policy from built-ins + optional JSON override."""
    selected = ["generic"]

    explicit = plugin_names or []
    for name in explicit:
        if name not in selected:
            selected.append(name)

    if (project_path / "artisan").exists() and "laravel" not in selected:
        selected.append("laravel")

    if project_path.name.lower() == "newerp" and "newerp" not in selected:
        selected.append("newerp")

    has_rails_app = (project_path / "bin" / "rails").exists() or (
        project_path / "config" / "application.rb"
    ).exists()
    has_rails_engine = any(project_path.glob("*.gemspec")) and any(
        candidate.exists()
        for candidate in (
            *project_path.glob("lib/**/*engine.rb"),
            *project_path.glob("lib/**/*railtie.rb"),
        )
    )
    if (has_rails_app or has_rails_engine) and "rails" not in selected:
        selected.append("rails")

    if (
        (project_path / "manage.py").exists()
        or (project_path / "django" / "__init__.py").exists()
    ) and "django" not in selected:
        selected.append("django")

    if (
        (
            (project_path / "wp-admin").exists()
            and (project_path / "wp-includes").exists()
        )
        or (
            (project_path / "src" / "wp-admin").exists()
            and (project_path / "src" / "wp-includes").exists()
        )
        and "wordpress" not in selected
    ):
        selected.append("wordpress")

    merged: dict[str, Any] = {
        "plugins_applied": selected,
    }

    for name in selected:
        payload = BUILTIN_PLUGINS.get(name)
        if payload is None:
            logger.warning("Unknown plugin '%s' ignored", name)
            continue
        merged = _deep_merge(merged, payload)

    loaded_plugins = list(external_plugins or [])
    if not loaded_plugins and plugin_modules:
        loaded_plugins = load_external_plugins(plugin_modules)

    for plugin in loaded_plugins:
        patch = build_policy_patch_from_plugin(
            plugin,
            project_path=project_path,
            selected_plugins=selected,
        )
        if patch:
            merged = _deep_merge(merged, patch)
            merged["plugins_applied"] = [
                *merged.get("plugins_applied", []),
                f"module:{plugin.ref}",
            ]

    default_policy = project_path / ".codexaudit" / "policy.json"
    if default_policy.exists():
        merged = _deep_merge(merged, _load_json_policy(default_policy))

    if policy_file and policy_file.exists():
        merged = _deep_merge(merged, _load_json_policy(policy_file))

    return AnalysisPolicy.model_validate(_normalize_policy(merged))
