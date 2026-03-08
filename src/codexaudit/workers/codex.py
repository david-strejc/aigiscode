"""Codex CLI worker for generating Semantic Envelopes.

Runs Codex CLI (or falls back to OpenAI API) to analyze individual files
and extract their semantic signatures as structured JSON.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
)

from codexaudit.ai.backends import generate_text, has_any_backend
from codexaudit.graph.analyzer import detect_layer_from_path
from codexaudit.indexer.store import IndexStore
from codexaudit.models import ArchitecturalLayer, FileInfo, SemanticEnvelope


# Type-specific analysis instructions
TYPE_INSTRUCTIONS: dict[ArchitecturalLayer, str] = {
    ArchitecturalLayer.CONTROLLER: "Route definitions, request validation, authorization checks, response formatting",
    ArchitecturalLayer.MIDDLEWARE: "Cross-cutting request pipeline behavior, authentication, rate limiting, and request mutation",
    ArchitecturalLayer.MODEL: "Eloquent relationships, fillable/guarded properties, scopes, accessors/mutators",
    ArchitecturalLayer.SERVICE: "Business logic, external service calls, transaction management",
    ArchitecturalLayer.VIEW: "Props, emits, composables used, external API calls",
    ArchitecturalLayer.MIGRATION: "Schema changes, table/column names, indexes",
    ArchitecturalLayer.REPOSITORY: "Data access patterns, query optimization, caching",
    ArchitecturalLayer.CONFIG: "Configuration keys, environment variables, default values",
    ArchitecturalLayer.UTILITY: "Primary responsibility and data flow",
    ArchitecturalLayer.UNKNOWN: "Primary responsibility and data flow",
}

PROMPT_TEMPLATE = """You are an expert static analysis engine. Your task is to analyze the provided source code file and extract its semantic signature into a strict JSON format.

FILE PATH: {filepath}
FILE TYPE HEURISTIC: {detected_file_type}

INSTRUCTIONS:
1. Do not explain the code. Output ONLY valid JSON.
2. Be concise. Use bullet points in string arrays.
3. Pay special attention to {type_specific_instructions}.

JSON SCHEMA TO RETURN:
{{
  "summary": "A 1-2 sentence description of the file's primary responsibility.",
  "architectural_layer": "Enum: [Controller, Middleware, Service, Model, Repository, View, Config, Utility, Unknown]",
  "public_api": [
    "List of public methods/functions with a brief note on what they do"
  ],
  "dependencies_intent": [
    "List the critical external dependencies and WHY they are used"
  ],
  "side_effects": [
    "List any database writes, external API calls, file system changes, or event dispatches."
  ],
  "anti_patterns_detected": [
    "List any obvious code smells. Leave empty if none."
  ]
}}

SOURCE CODE:
```
{code}
```"""


def _build_prompt(filepath: str, code: str, layer: ArchitecturalLayer) -> str:
    """Build the analysis prompt for a file."""
    type_instructions = TYPE_INSTRUCTIONS.get(
        layer, TYPE_INSTRUCTIONS[ArchitecturalLayer.UNKNOWN]
    )

    return PROMPT_TEMPLATE.format(
        filepath=filepath,
        detected_file_type=layer.value,
        type_specific_instructions=type_instructions,
        code=code,
    )


def _parse_envelope_response(response_text: str) -> dict:
    """Parse the LLM response into an envelope dict.

    Handles JSON potentially wrapped in markdown code blocks.
    """
    text = response_text.strip()

    # Remove markdown code block wrapper if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    return data


async def create_semantic_envelope(
    filepath: str,
    project_path: Path,
    layer: ArchitecturalLayer,
    model: str = "gpt-5.3-codex",
) -> SemanticEnvelope | None:
    """Analyze a single file and return its semantic envelope.

    Tries Codex CLI first, then falls back to OpenAI API.
    """
    full_path = project_path / filepath
    try:
        code = full_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, IOError):
        return None

    # Truncate very large files to avoid token limits
    max_chars = 15000
    if len(code) > max_chars:
        code = code[:max_chars] + "\n... [TRUNCATED]"

    prompt = _build_prompt(filepath, code, layer)

    response, _backend = await generate_text(
        system="You are an expert code analysis engine. Output only valid JSON.",
        user=prompt,
        model=model,
        allow_codex_cli_fallback=True,
        allow_claude_fallback=False,
        reasoning_effort="medium",
    )

    if response is None:
        return None

    data = _parse_envelope_response(response)
    if not data:
        return None

    try:
        layer_value = data.get("architectural_layer", "Unknown")
        try:
            arch_layer = ArchitecturalLayer(layer_value)
        except ValueError:
            arch_layer = ArchitecturalLayer.UNKNOWN

        return SemanticEnvelope(
            summary=data.get("summary", ""),
            architectural_layer=arch_layer,
            public_api=data.get("public_api", []),
            dependencies_intent=data.get("dependencies_intent", []),
            side_effects=data.get("side_effects", []),
            anti_patterns=data.get("anti_patterns_detected", []),
        )
    except Exception:
        return None


async def process_files(
    store: IndexStore,
    project_path: Path,
    max_workers: int = 4,
    model: str = "gpt-5.3-codex",
) -> int:
    """Process all indexed files to generate semantic envelopes.

    Returns the number of envelopes generated.
    """
    files = store.get_all_files()

    if not has_any_backend(allow_codex_cli_fallback=True, allow_claude_fallback=False):
        return 0

    generated = 0
    semaphore = asyncio.Semaphore(max_workers)

    async def process_one(file_info: FileInfo) -> None:
        nonlocal generated
        async with semaphore:
            layer = detect_layer_from_path(file_info.path)
            envelope = await create_semantic_envelope(
                file_info.path,
                project_path,
                layer,
                model=model,
            )
            if envelope:
                envelope.file_id = file_info.id
                store.upsert_envelope(envelope)
                generated += 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("Generating semantic envelopes", total=len(files))

        # Process in batches to update progress
        tasks = []
        for file_info in files:
            tasks.append(process_one(file_info))

        # Run all tasks, advancing progress as each completes
        for coro in asyncio.as_completed(tasks):
            await coro
            progress.advance(task)

    return generated
