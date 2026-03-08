"""Analytical mode for AI-assisted policy tuning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codexaudit.ai.backends import generate_text
from codexaudit.policy.models import AnalysisPolicy

ANALYTICAL_SYSTEM_PROMPT = """You are configuring a static-analysis policy for a real production codebase.
Return strict JSON only.

Goal:
- maximize signal quality
- reduce false positives
- avoid broad suppressions
- keep findings actionable

Output schema:
{
  "graph": {
    "js_fuzzy_import_resolution": boolean,
    "js_import_aliases": {"prefix": "path/"},
    "layer_patterns": {"path-token": "LayerName"},
    "orphan_entry_patterns": ["glob"]
  },
  "dead_code": {
    "attribute_usage_names": ["Name"],
    "abandoned_languages": ["php"],
    "abandoned_entry_patterns": ["/Path/"],
    "abandoned_dynamic_reference_patterns": ["glob"]
  },
  "hardwiring": {
    "entity_context_allow_regexes": ["regex"],
    "repeated_literal_min_occurrences": 3,
    "repeated_literal_min_length": 4,
    "skip_path_patterns": ["app/Console/*"],
    "js_env_allow_names": ["DEV"]
  },
  "ai": {
    "allow_claude_fallback": boolean
  }
}
"""


def _build_prompt(
    project_path: Path, report_summary: dict[str, Any], base_policy: AnalysisPolicy
) -> str:
    return (
        f"Project: {project_path}\n"
        f"Current policy: {base_policy.model_dump_json(indent=2)}\n"
        f"Current report summary: {json.dumps(report_summary, indent=2)}\n"
        "Return a policy patch JSON that improves precision for this project."
    )


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                return {}
        return {}


async def propose_policy_patch(
    project_path: Path,
    report_summary: dict[str, Any],
    base_policy: AnalysisPolicy,
) -> tuple[dict[str, Any], str]:
    """Ask Codex for a policy patch; fallback to empty patch if unavailable."""
    prompt = _build_prompt(project_path, report_summary, base_policy)

    text, backend = await generate_text(
        system=ANALYTICAL_SYSTEM_PROMPT,
        user=prompt,
        model=base_policy.ai.codex_model,
        allow_codex_cli_fallback=base_policy.ai.allow_codex_cli_fallback,
        allow_claude_fallback=False,
        reasoning_effort="medium",
    )
    if text is None:
        return {}, "none"

    return _extract_json(text), backend


def save_policy_patch(output_dir: Path, patch: dict[str, Any]) -> Path:
    """Persist AI-generated policy patch for manual review and reuse."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "policy.suggested.json"
    path.write_text(
        json.dumps(patch, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path
