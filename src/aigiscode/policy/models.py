"""Policy models for plugin-driven analysis behavior."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GraphPolicy(BaseModel):
    """Graph-building and architectural analysis behavior."""

    js_fuzzy_import_resolution: bool = False
    js_import_aliases: dict[str, str] = Field(default_factory=dict)
    layer_patterns: dict[str, str] = Field(default_factory=dict)
    layer_violation_excludes: list[str] = Field(default_factory=list)
    orphan_entry_patterns: list[str] = Field(default_factory=list)


class DeadCodePolicy(BaseModel):
    """Dead-code detector behavior."""

    attribute_usage_names: list[str] = Field(default_factory=lambda: ["Override"])
    abandoned_languages: list[str] = Field(default_factory=lambda: ["php", "rust"])
    abandoned_entry_patterns: list[str] = Field(default_factory=list)
    abandoned_dynamic_reference_patterns: list[str] = Field(default_factory=list)


class HardwiringPolicy(BaseModel):
    """Hardwiring detector behavior."""

    entity_context_require_regexes: list[str] = Field(default_factory=list)
    entity_context_allow_regexes: list[str] = Field(default_factory=list)
    low_signal_literals: list[str] = Field(default_factory=list)
    magic_string_min_length: int = 5
    magic_string_skip_path_patterns: list[str] = Field(default_factory=list)
    magic_string_signal_context_regexes: list[str] = Field(default_factory=list)
    magic_string_noise_context_regexes: list[str] = Field(default_factory=list)
    repeated_literal_min_occurrences: int = 3
    repeated_literal_min_length: int = 4
    repeated_literal_min_distinct_dirs: int = 2
    repeated_literal_require_compound: bool = True
    repeated_literal_skip_regexes: list[str] = Field(default_factory=list)
    skip_path_patterns: list[str] = Field(default_factory=list)
    js_env_allow_names: list[str] = Field(
        default_factory=lambda: ["DEV", "PROD", "MODE", "SSR", "BASE_URL", "NODE_ENV"]
    )


class AIPolicy(BaseModel):
    """AI backend behavior and ordering."""

    primary_backend: str = "codex_sdk"
    allow_codex_cli_fallback: bool = True
    allow_claude_fallback: bool = True
    codex_model: str = "gpt-5.3-codex"
    synthesis_model: str = "gpt-5.3-codex"
    review_model: str = "gpt-5.3-codex"


class AnalysisPolicy(BaseModel):
    """Full runtime policy produced by merging plugins + optional overrides."""

    plugins_applied: list[str] = Field(default_factory=list)
    graph: GraphPolicy = Field(default_factory=GraphPolicy)
    dead_code: DeadCodePolicy = Field(default_factory=DeadCodePolicy)
    hardwiring: HardwiringPolicy = Field(default_factory=HardwiringPolicy)
    ai: AIPolicy = Field(default_factory=AIPolicy)
