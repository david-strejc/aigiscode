"""Tests for aigiscode.ai.backends — describe_backend_order & has_any_backend."""

from __future__ import annotations

import shutil

from aigiscode.ai import backends


# ---------------------------------------------------------------------------
# describe_backend_order
# ---------------------------------------------------------------------------

class TestDescribeBackendOrder:
    """Tests for describe_backend_order."""

    def test_all_backends_available(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        result = backends.describe_backend_order(
            primary_backend="codex",
            allow_codex_cli_fallback=True,
            allow_claude_fallback=True,
        )
        assert result == "Codex SDK \u2192 Codex CLI \u2192 Claude"

    def test_only_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        result = backends.describe_backend_order(
            primary_backend="codex",
            allow_codex_cli_fallback=True,
            allow_claude_fallback=True,
        )
        assert result == "Codex SDK"

    def test_no_backends(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        result = backends.describe_backend_order()
        assert result == "no backend available"

    def test_non_codex_primary_skips_sdk(self, monkeypatch):
        """When primary_backend is not 'codex', Codex SDK should be skipped."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        result = backends.describe_backend_order(
            primary_backend="claude",
            allow_codex_cli_fallback=True,
            allow_claude_fallback=True,
        )
        # Codex SDK should NOT appear since primary_backend != "codex"
        assert "Codex SDK" not in result
        assert "Codex CLI" in result
        assert "Claude" in result

    def test_disable_codex_cli_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        result = backends.describe_backend_order(
            primary_backend="codex",
            allow_codex_cli_fallback=False,
            allow_claude_fallback=True,
        )
        assert result == "Codex SDK \u2192 Claude"

    def test_disable_claude_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        result = backends.describe_backend_order(
            primary_backend="codex",
            allow_codex_cli_fallback=True,
            allow_claude_fallback=False,
        )
        assert result == "Codex SDK \u2192 Codex CLI"


# ---------------------------------------------------------------------------
# has_any_backend
# ---------------------------------------------------------------------------

class TestHasAnyBackend:
    """Tests for has_any_backend."""

    def test_openai_key_with_codex_primary(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        assert backends.has_any_backend(primary_backend="codex") is True

    def test_openai_key_with_non_codex_primary(self, monkeypatch):
        """OpenAI key present but primary_backend is not codex -> SDK check skipped."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        # With no codex CLI and no anthropic key, and primary != codex,
        # there should be no backend available.
        assert backends.has_any_backend(
            primary_backend="claude",
            allow_codex_cli_fallback=True,
            allow_claude_fallback=False,
        ) is False

    def test_codex_cli_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/codex" if cmd == "codex" else None)
        assert backends.has_any_backend(primary_backend="codex") is True

    def test_claude_fallback(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        assert backends.has_any_backend(
            primary_backend="codex",
            allow_codex_cli_fallback=False,
            allow_claude_fallback=True,
        ) is True

    def test_no_backends(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        assert backends.has_any_backend() is False

    def test_all_fallbacks_disabled_no_primary(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        assert backends.has_any_backend(
            primary_backend="codex",
            allow_codex_cli_fallback=False,
            allow_claude_fallback=False,
        ) is False
