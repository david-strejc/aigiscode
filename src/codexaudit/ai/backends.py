"""Shared AI backend adapters.

Centralizes model calls so workers/review/synthesis/analytical paths stay DRY
and backend selection is configurable by policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)


def _has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _has_anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def has_any_backend(
    allow_codex_cli_fallback: bool = True, allow_claude_fallback: bool = True
) -> bool:
    """Return True when at least one configured backend is available."""
    if _has_openai_key():
        return True
    if allow_codex_cli_fallback and shutil.which("codex") is not None:
        return True
    if allow_claude_fallback and _has_anthropic_key():
        return True
    return False


async def call_codex_sdk(
    system: str,
    user: str,
    model: str,
    reasoning_effort: str = "medium",
) -> str | None:
    """Call Codex/OpenAI via Responses API."""
    if not _has_openai_key():
        return None

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            reasoning={"effort": reasoning_effort},
        )
        return response.output_text or None
    except Exception as exc:
        logger.warning("Codex SDK call failed: %s", exc)
        return None


async def call_codex_cli(
    prompt: str,
    model: str,
    timeout_seconds: int = 180,
) -> str | None:
    """Call Codex CLI non-interactively and extract assistant text from JSONL events."""
    codex_path = shutil.which("codex")
    if not codex_path:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            codex_path,
            "exec",
            "--model",
            model,
            "--sandbox",
            "read-only",
            "--json",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )

        if proc.returncode != 0 or not stdout:
            return None

        text_parts: list[str] = []
        for raw in stdout.decode("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            if item.get("type") != "agent_message":
                continue
            text = item.get("text", "")
            if text:
                text_parts.append(text)

        return "\n".join(text_parts) if text_parts else None
    except Exception as exc:
        logger.warning("Codex CLI call failed: %s", exc)
        return None


async def call_claude(
    system: str,
    user: str,
    model: str = "claude-sonnet-4-20250514",
) -> str | None:
    """Call Anthropic Messages API."""
    if not _has_anthropic_key():
        return None

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.content:
            return response.content[0].text
        return None
    except Exception as exc:
        logger.warning("Claude call failed: %s", exc)
        return None


async def generate_text(
    system: str,
    user: str,
    model: str,
    allow_codex_cli_fallback: bool = True,
    allow_claude_fallback: bool = True,
    reasoning_effort: str = "medium",
    claude_model: str = "claude-sonnet-4-20250514",
) -> tuple[str | None, str]:
    """Generate text with ordered fallbacks.

    Order:
    1) Codex SDK (Responses API)
    2) Codex CLI (optional)
    3) Claude (optional)
    """
    text = await call_codex_sdk(
        system, user, model=model, reasoning_effort=reasoning_effort
    )
    if text:
        return text, "codex_sdk"

    if allow_codex_cli_fallback:
        prompt = f"{system}\n\n{user}"
        text = await call_codex_cli(prompt, model=model)
        if text:
            return text, "codex_cli"

    if allow_claude_fallback:
        text = await call_claude(system, user, model=claude_model)
        if text:
            return text, "claude"

    return None, "none"
