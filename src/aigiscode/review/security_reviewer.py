"""AI-powered security finding reviewer.

Classifies external security findings as actionable, accepted_noise,
or needs_context using the same AI backends as the main finding reviewer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aigiscode.ai.backends import generate_text
from aigiscode.models import (
    ExternalAnalysisResult,
    FindingVerdict,
    ReviewResult,
)
from aigiscode.rules.engine import Rule

logger = logging.getLogger(__name__)

MAX_SAMPLE = 20

SECURITY_REVIEW_SYSTEM_PROMPT = """\
You are an expert security reviewer triaging findings from automated security scanners.

Your task: classify each finding as one of:
- **true_positive**: A real security issue that should be fixed.
- **false_positive**: A false alarm — the code is safe due to context the scanner cannot see.
- **needs_context**: Cannot determine without more information.

Respond with valid JSON only:
{
  "verdicts": [
    {
      "index": 0,
      "verdict": "true_positive|false_positive|needs_context",
      "reason": "brief explanation"
    }
  ]
}
"""


async def review_external_security_findings(
    external_analysis: ExternalAnalysisResult,
    *,
    project_path: str | Path,
    project_type: str = "mixed-language project",
    review_model: str = "gpt-5.3-codex",
    primary_backend: str = "codex",
    allow_claude_fallback: bool = True,
) -> tuple[ReviewResult, list[Rule]]:
    """Review external security findings using AI."""
    project_path = Path(project_path)
    security_findings = [
        f for f in external_analysis.findings if f.domain == "security"
    ]

    if not security_findings:
        return ReviewResult(), []

    sampled = security_findings[:MAX_SAMPLE]

    parts = [
        f"## Project Type: {project_type}",
        f"## Security Findings ({len(security_findings)} total, {len(sampled)} sampled)",
        "",
    ]
    for i, finding in enumerate(sampled):
        parts.append(f"### Finding #{i}")
        parts.append(f"**Tool**: {finding.tool}")
        parts.append(f"**Rule**: {finding.rule_id}")
        parts.append(f"**File**: `{finding.file_path}:{finding.line}`")
        parts.append(f"**Severity**: {finding.severity}")
        parts.append(f"**Message**: {finding.message}")
        code = _read_code_context(project_path, finding.file_path, finding.line)
        if code:
            parts.append(f"**Source**:\n```\n{code}\n```")
        parts.append("")

    prompt = "\n".join(parts)

    response, backend = await generate_text(
        SECURITY_REVIEW_SYSTEM_PROMPT,
        prompt,
        model=review_model,
        allow_codex_cli_fallback=True,
        allow_claude_fallback=allow_claude_fallback,
        reasoning_effort="medium",
    )

    all_verdicts: list[FindingVerdict] = []
    all_rules: list[Rule] = []

    if not response:
        for f in sampled:
            all_verdicts.append(
                FindingVerdict(
                    file_path=f.file_path,
                    line=f.line,
                    category=f.category or "security",
                    verdict="needs_context",
                    reason="AI review unavailable",
                )
            )
    else:
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            data = json.loads(text)
            for v in data.get("verdicts", []):
                idx = v.get("index", -1)
                if idx < 0 or idx >= len(sampled):
                    continue
                finding = sampled[idx]
                all_verdicts.append(
                    FindingVerdict(
                        file_path=finding.file_path,
                        line=finding.line,
                        category=finding.category or "security",
                        verdict=v.get("verdict", "needs_context"),
                        reason=v.get("reason", ""),
                    )
                )
        except json.JSONDecodeError:
            logger.warning("Failed to parse security review AI response")

    tp = sum(1 for v in all_verdicts if v.verdict == "true_positive")
    fp = sum(1 for v in all_verdicts if v.verdict == "false_positive")
    nc = sum(1 for v in all_verdicts if v.verdict == "needs_context")

    result = ReviewResult(
        total_reviewed=len(sampled),
        true_positives=tp,
        false_positives=fp,
        needs_context=nc,
        rules_generated=len(all_rules),
        verdicts=all_verdicts,
    )
    # cli.py accesses result.actionable and result.accepted_noise
    # Use object.__setattr__ to bypass Pydantic's strict field validation
    object.__setattr__(result, "actionable", tp)
    object.__setattr__(result, "accepted_noise", fp)

    return result, all_rules


def _read_code_context(
    project_path: Path, file_path: str, line: int, context: int = 5
) -> str:
    full_path = project_path / file_path
    if not full_path.exists():
        return ""
    try:
        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, line - context - 1)
        end = min(len(lines), line + context)
        numbered = []
        for i, ln in enumerate(lines[start:end], start=start + 1):
            marker = ">>>" if i == line else "   "
            numbered.append(f"{marker} {i:4d} | {ln}")
        return "\n".join(numbered)
    except Exception:
        return ""
