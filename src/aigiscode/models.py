"""Pydantic models for all aigiscode data structures."""

from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# --- Enums ---


class Language(str, enum.Enum):
    PHP = "php"
    PYTHON = "python"
    RUBY = "ruby"
    RUST = "rust"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    VUE = "vue"
    JSON = "json"
    UNKNOWN = "unknown"


class SymbolType(str, enum.Enum):
    CLASS = "class"
    MODULE = "module"
    METHOD = "method"
    FUNCTION = "function"
    PROPERTY = "property"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"


class DependencyType(str, enum.Enum):
    IMPORT = "import"
    LOAD = "load"
    REGISTER = "register"
    CALL = "call"
    INHERIT = "inherit"
    IMPLEMENT = "implement"


class Visibility(str, enum.Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class ArchitecturalLayer(str, enum.Enum):
    CONTROLLER = "Controller"
    MIDDLEWARE = "Middleware"
    SERVICE = "Service"
    MODEL = "Model"
    REPOSITORY = "Repository"
    VIEW = "View"
    CONFIG = "Config"
    UTILITY = "Utility"
    MIGRATION = "Migration"
    UNKNOWN = "Unknown"


# --- File-level models ---


class FileInfo(BaseModel):
    """Represents a parsed source file."""

    id: int | None = None
    path: str
    language: Language
    size: int
    last_modified: datetime | None = None


# --- Symbol models ---


class SymbolInfo(BaseModel):
    """Represents an extracted code symbol."""

    id: int | None = None
    file_id: int | None = None
    type: SymbolType
    name: str
    namespace: str | None = None
    visibility: Visibility = Visibility.UNKNOWN
    line_start: int
    line_end: int
    parent_symbol_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Dependency models ---


class DependencyInfo(BaseModel):
    """Represents a dependency between files/symbols."""

    id: int | None = None
    source_file_id: int | None = None
    target_name: str
    type: DependencyType
    line: int


# --- Semantic Envelope ---


class SemanticEnvelope(BaseModel):
    """The AI-generated semantic signature of a file."""

    id: int | None = None
    file_id: int | None = None
    summary: str = ""
    architectural_layer: ArchitecturalLayer = ArchitecturalLayer.UNKNOWN
    public_api: list[str] = Field(default_factory=list)
    dependencies_intent: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)


# --- Graph metrics ---


class CouplingMetrics(BaseModel):
    """Afferent and efferent coupling for a module."""

    module: str
    afferent: int = 0  # Ca - incoming dependencies
    efferent: int = 0  # Ce - outgoing dependencies
    instability: float = 0.0  # Ce / (Ca + Ce)


class GodClass(BaseModel):
    """A class identified as potentially too large / too coupled."""

    name: str
    file_path: str
    method_count: int
    dependency_count: int
    line_count: int


class LayerViolation(BaseModel):
    """A dependency that violates the expected layer ordering."""

    source_file: str
    source_layer: ArchitecturalLayer
    target_name: str
    target_layer: ArchitecturalLayer
    violation: str


class GraphAnalysisResult(BaseModel):
    """Full result of graph analysis phase."""

    circular_dependencies: list[list[str]] = Field(default_factory=list)
    strong_circular_dependencies: list[list[str]] = Field(default_factory=list)
    coupling_metrics: list[CouplingMetrics] = Field(default_factory=list)
    god_classes: list[GodClass] = Field(default_factory=list)
    bottleneck_files: list[tuple[str, float]] = Field(default_factory=list)
    layer_violations: list[LayerViolation] = Field(default_factory=list)
    orphan_files: list[str] = Field(default_factory=list)
    runtime_entry_candidates: list[str] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0


# --- External analysis models ---


class ExternalFinding(BaseModel):
    """A single finding from an external analysis tool."""

    tool: str
    rule_id: str = ""
    file_path: str = ""
    line: int = 0
    message: str = ""
    severity: str = "medium"
    domain: str = "security"
    category: str = ""
    fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalToolRun(BaseModel):
    """Record of a single external tool execution."""

    tool: str
    command: list[str] = Field(default_factory=list)
    status: str = "pending"
    findings_count: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
    version: str = ""


class ExternalAnalysisResult(BaseModel):
    """Aggregate result of all external tool runs."""

    tool_runs: list[ExternalToolRun] = Field(default_factory=list)
    findings: list[ExternalFinding] = Field(default_factory=list)


class FeedbackLoop(BaseModel):
    """Metrics for the feedback loop between detection and policy."""

    detected_total: int = 0
    actionable_visible: int = 0
    accepted_by_policy: int = 0
    rules_generated: int = 0


# --- Report models ---


class ReportData(BaseModel):
    """Structured data for the full report."""

    project_path: str
    generated_at: datetime = Field(default_factory=datetime.now)
    files_indexed: int = 0
    symbols_extracted: int = 0
    dependencies_found: int = 0
    unsupported_source_files: int = 0
    unsupported_language_breakdown: dict[str, int] = Field(default_factory=dict)
    detector_coverage: dict[str, list[str]] = Field(default_factory=dict)
    graph_analysis: GraphAnalysisResult = Field(default_factory=GraphAnalysisResult)
    envelopes_generated: int = 0
    synthesis: str = ""
    language_breakdown: dict[str, int] = Field(default_factory=dict)
    dead_code: Any | None = None  # DeadCodeResult from graph.deadcode
    hardwiring: Any | None = None  # HardwiringResult from graph.hardwiring
    review: Any | None = None  # ReviewResult from review.ai_reviewer
    extensions: dict[str, Any] = Field(default_factory=dict)
    feedback_loop: FeedbackLoop = Field(default_factory=FeedbackLoop)


# --- AI Review models ---


class FindingVerdict(BaseModel):
    """AI verdict for a single static analysis finding."""

    file_path: str
    line: int
    category: str
    name: str = ""
    value: str = ""
    verdict: str  # true_positive | false_positive | needs_context
    reason: str = ""


class ReviewResult(BaseModel):
    """Aggregate result of AI finding review."""

    total_reviewed: int = 0
    true_positives: int = 0
    false_positives: int = 0
    needs_context: int = 0
    rules_generated: int = 0
    rules_prefiltered: int = 0
    verdicts: list[FindingVerdict] = Field(default_factory=list)


# --- Config ---


class AigisCodeConfig(BaseModel):
    """Runtime configuration."""

    project_path: Path
    output_dir: Path | None = None
    policy_file: Path | None = None
    max_workers: int = 4
    skip_ai: bool = False
    skip_synthesis: bool = False
    skip_review: bool = False
    analytical_mode: bool = False
    plugins: list[str] = Field(default_factory=list)
    plugin_modules: list[str] = Field(default_factory=list)
    languages: list[Language] = Field(
        default_factory=lambda: [
            Language.PHP,
            Language.PYTHON,
            Language.RUBY,
            Language.RUST,
            Language.TYPESCRIPT,
            Language.JAVASCRIPT,
            Language.VUE,
        ]
    )
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [
            "vendor",
            "node_modules",
            ".git",
            "storage",
            "public/build",
            "public/vendor",
            ".aigiscode",
            "__pycache__",
            ".mypy_cache",
            "tmp",
            "dist",
            "build",
        ]
    )

    @property
    def db_path(self) -> Path:
        return self.effective_output_dir / "aigiscode.db"

    @property
    def effective_output_dir(self) -> Path:
        if self.output_dir:
            return self.output_dir
        return self.project_path / ".aigiscode"

    @property
    def is_laravel(self) -> bool:
        return (self.project_path / "artisan").exists()
