"""Runtime loading and hook execution for external analysis plugins."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codexaudit.contracts import ContractLookup, merge_contract_lookup

logger = logging.getLogger(__name__)


@dataclass
class ExternalPlugin:
    """Loaded external plugin module and its stable identifier."""

    ref: str
    module: Any
    name: str


def load_external_plugins(module_refs: list[str] | None) -> list[ExternalPlugin]:
    """Load external plugin modules once so policy and analysis share them."""
    plugins: list[ExternalPlugin] = []
    for module_ref in module_refs or []:
        module = _import_plugin_module(module_ref)
        if module is None:
            continue
        plugin_obj = getattr(module, "PLUGIN", None)
        plugin_name = getattr(plugin_obj, "name", None) or getattr(
            module, "PLUGIN_NAME", None
        )
        plugins.append(
            ExternalPlugin(
                ref=module_ref,
                module=module,
                name=str(plugin_name or module_ref),
            )
        )
    return plugins


def build_policy_patch_from_plugin(
    plugin: ExternalPlugin,
    *,
    project_path: Path,
    selected_plugins: list[str],
) -> dict[str, Any]:
    """Call the policy hook if the plugin provides one."""
    hook = _resolve_plugin_hook(plugin, "build_policy_patch")
    if hook is None:
        return {}
    try:
        patch = hook(
            project_path=project_path,
            selected_plugins=list(selected_plugins),
        )
    except TypeError:
        try:
            patch = hook(project_path, list(selected_plugins))
        except TypeError as exc:
            logger.warning(
                "External plugin '%s' policy hook has incompatible signature: %s",
                plugin.ref,
                exc,
            )
            return {}
        except Exception as exc:
            logger.warning(
                "External plugin '%s' policy hook failed: %s",
                plugin.ref,
                exc,
            )
            return {}
    except Exception as exc:
        logger.warning(
            "External plugin '%s' policy hook failed: %s",
            plugin.ref,
            exc,
        )
        return {}
    if patch is None:
        return {}
    if not isinstance(patch, dict):
        logger.warning(
            "External plugin '%s' returned non-dict policy patch, ignored",
            plugin.ref,
        )
        return {}
    return patch


def apply_graph_result_plugins(
    graph_result,
    plugins: list[ExternalPlugin],
    *,
    graph,
    store,
    project_path: Path,
    policy,
):
    """Let plugins refine graph analysis output."""
    result = graph_result
    for plugin in plugins:
        candidate = invoke_plugin_hook(
            plugin,
            "refine_graph_result",
            graph_result=result,
            graph=graph,
            store=store,
            project_path=project_path,
            policy=policy,
        )
        if candidate is not None:
            result = candidate
    return result


def apply_dead_code_result_plugins(
    dead_code_result,
    plugins: list[ExternalPlugin],
    *,
    store,
    project_path: Path,
    policy,
):
    """Let plugins refine dead-code findings."""
    result = dead_code_result
    for plugin in plugins:
        candidate = invoke_plugin_hook(
            plugin,
            "refine_dead_code_result",
            dead_code_result=result,
            store=store,
            project_path=project_path,
            policy=policy,
        )
        if candidate is not None:
            result = candidate
    return result


def apply_hardwiring_result_plugins(
    hardwiring_result,
    plugins: list[ExternalPlugin],
    *,
    store,
    project_path: Path,
    policy,
):
    """Let plugins refine hardwiring findings."""
    result = hardwiring_result
    for plugin in plugins:
        candidate = invoke_plugin_hook(
            plugin,
            "refine_hardwiring_result",
            hardwiring_result=result,
            store=store,
            project_path=project_path,
            policy=policy,
        )
        if candidate is not None:
            result = candidate
    return result


def apply_contract_lookup_plugins(
    contract_lookup: ContractLookup,
    plugins: list[ExternalPlugin],
    *,
    store,
    project_path: Path,
    policy,
) -> ContractLookup:
    """Let plugins enrich declared runtime contracts before hardwiring runs."""
    result = contract_lookup
    for plugin in plugins:
        candidate = invoke_plugin_hook(
            plugin,
            "refine_contract_lookup",
            contract_lookup=result,
            store=store,
            project_path=project_path,
            policy=policy,
        )
        if candidate is not None:
            result = merge_contract_lookup(result, candidate)
    return result


def apply_hardwiring_finding_plugins(
    findings,
    plugins: list[ExternalPlugin],
    *,
    category: str,
    store,
    project_path: Path,
    policy,
    contract_lookup: ContractLookup,
):
    """Let plugins refine a hardwiring category before the final result is built."""
    result = findings
    for plugin in plugins:
        candidate = invoke_plugin_hook(
            plugin,
            "refine_hardwiring_findings",
            findings=result,
            category=category,
            store=store,
            project_path=project_path,
            policy=policy,
            contract_lookup=contract_lookup,
        )
        if candidate is not None:
            result = candidate
    return result


def build_report_extensions(
    plugins: list[ExternalPlugin],
    *,
    report,
    graph,
    store,
    project_path: Path,
    policy,
) -> dict[str, Any]:
    """Collect plugin-defined report payloads under a dedicated section."""
    extensions: dict[str, Any] = {}
    for plugin in plugins:
        payload = invoke_plugin_hook(
            plugin,
            "build_report_extensions",
            report=report,
            graph=graph,
            store=store,
            project_path=project_path,
            policy=policy,
        )
        if payload is None:
            continue
        if isinstance(payload, dict):
            extensions[plugin.name] = payload
        else:
            extensions[plugin.name] = {"result": payload}
    return extensions


def invoke_plugin_hook(plugin: ExternalPlugin, hook_name: str, **kwargs) -> Any | None:
    """Call a plugin hook if present. Returning None means "no change"."""
    hook = _resolve_plugin_hook(plugin, hook_name)
    if hook is None:
        return None
    try:
        return hook(**kwargs)
    except TypeError as exc:
        logger.warning(
            "External plugin '%s' hook '%s' has incompatible signature: %s",
            plugin.ref,
            hook_name,
            exc,
        )
    except Exception as exc:
        logger.warning(
            "External plugin '%s' hook '%s' failed: %s",
            plugin.ref,
            hook_name,
            exc,
        )
    return None


def _resolve_plugin_hook(plugin: ExternalPlugin, hook_name: str) -> Any | None:
    plugin_obj = getattr(plugin.module, "PLUGIN", None)
    if plugin_obj is not None:
        hook = getattr(plugin_obj, hook_name, None)
        if callable(hook):
            return hook

    hook = getattr(plugin.module, hook_name, None)
    if callable(hook):
        return hook
    return None


def _import_plugin_module(module_ref: str) -> Any | None:
    try:
        as_path = Path(module_ref)
        if as_path.exists():
            spec = importlib.util.spec_from_file_location(
                f"codexaudit_ext_{as_path.stem}",
                as_path,
            )
            if spec is None or spec.loader is None:
                logger.warning("Could not load plugin module from path: %s", module_ref)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[attr-defined]
            return module
        return importlib.import_module(module_ref)
    except Exception as exc:
        logger.warning("Failed to import plugin module '%s': %s", module_ref, exc)
        return None
