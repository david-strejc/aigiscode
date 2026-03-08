"""Built-in runtime plugins bound to named policy profiles."""

from __future__ import annotations

import re

from codexaudit.extensions import ExternalPlugin


class _DjangoRuntimePlugin:
    name = "django-runtime"

    _core_env_paths = (
        "django/core/checks/",
        "django/core/management/",
        "django/utils/autoreload.py",
        "django/db/backends/base/creation.py",
    )
    _core_env_tokens = (
        "DJANGO_",
        "PYTHONSTARTUP",
        "PATH",
        "PATHEXT",
        "RUNNING_DJANGOS_TEST_SUITE",
    )

    def refine_hardwiring_findings(self, findings, category, **kwargs):
        if category == "env_outside_config":
            return [f for f in findings if not self._is_env_noise(f)]
        if category == "hardcoded_ip_url":
            return [f for f in findings if not self._is_url_noise(f)]
        return findings

    def _is_env_noise(self, finding) -> bool:
        path = finding.file_path.replace("\\", "/")
        context = finding.context.upper()
        return any(marker in path for marker in self._core_env_paths) or any(
            token in context for token in self._core_env_tokens
        )

    def _is_url_noise(self, finding) -> bool:
        path = finding.file_path.replace("\\", "/")
        return (
            path.startswith("docs/")
            or "/docs/" in path
            or "/_ext/" in path
            or "%s" in finding.value
            or "%s" in finding.context
        )


class _WordPressRuntimePlugin:
    name = "wordpress-runtime"

    _slug_like = re.compile(
        r"^(?:[a-z0-9/]+(?:[-_][a-z0-9/]+)+|[a-z0-9_]+:[a-z0-9_]+|[a-z0-9_-]+-)$",
        re.IGNORECASE,
    )
    _ui_context = re.compile(
        r"\b(?:act|action|screen|page|postbox|status|bulk|format|"
        r"theme_location|id|slug|tab|view|hook_suffix|pagenow|"
        r"adminpage|templateid|control|speed|name)\b",
        re.IGNORECASE,
    )
    _css_parser_literals = {
        "no-repeat",
        "waiting-for-directory-sizes",
    }
    _doc_url_files = {
        "src/index.php",
        "src/wp-admin/about.php",
        "src/wp-admin/includes/media.php",
    }
    _doc_hosts = (
        "wordpress.org",
        "planet.wordpress.org",
        "npmjs.com",
        "github.com",
    )

    def refine_hardwiring_findings(self, findings, category, **kwargs):
        if category == "magic_string":
            return [f for f in findings if not self._is_magic_noise(f)]
        if category == "hardcoded_ip_url":
            return [f for f in findings if not self._is_url_noise(f)]
        return findings

    def _is_magic_noise(self, finding) -> bool:
        path = finding.file_path.replace("\\", "/")
        context = finding.context
        if finding.value in self._css_parser_literals:
            return True
        if (
            (
                path.startswith("src/js/")
                or "/src/js/" in path
                or path.startswith("src/wp-admin/")
                or "/wp-admin/" in path
            )
            and self._slug_like.fullmatch(finding.value)
            and self._ui_context.search(context)
        ):
            return True
        if finding.value.startswith("/wp_") and (
            path.startswith("src/wp-admin/") or "/wp-admin/" in path
        ):
            return True
        return False

    def _is_url_noise(self, finding) -> bool:
        path = finding.file_path.replace("\\", "/")
        return (
            path in self._doc_url_files
            or "$src" in finding.context
            or "%1$s" in finding.value
            or (
                any(host in finding.value for host in self._doc_hosts)
                and any(
                    token in finding.context
                    for token in ("__(", "esc_url(", "Thank you")
                )
            )
            or (path.startswith("tools/") and "github.com" in finding.value)
        )


_BUILTIN_RUNTIME_PLUGIN_FACTORIES = {
    "django": _DjangoRuntimePlugin,
    "wordpress": _WordPressRuntimePlugin,
}


def load_builtin_runtime_plugins(selected_plugins: list[str]) -> list[ExternalPlugin]:
    """Return built-in runtime plugins for selected named profiles."""
    plugins: list[ExternalPlugin] = []
    for name in selected_plugins:
        factory = _BUILTIN_RUNTIME_PLUGIN_FACTORIES.get(name)
        if factory is None:
            continue
        plugin_obj = factory()
        plugins.append(
            ExternalPlugin(
                ref=f"builtin:{name}",
                module=plugin_obj,
                name=plugin_obj.name,
            )
        )
    return plugins
