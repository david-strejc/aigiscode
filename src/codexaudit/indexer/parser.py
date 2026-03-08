"""Source parser and indexer.

Handles file discovery, language detection, parsing, and orchestrates
symbol extraction for PHP, Python, Ruby, TypeScript, JavaScript, and Vue files.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
)

from codexaudit.models import (
    CodexAuditConfig,
    DependencyInfo,
    FileInfo,
    Language,
    SymbolInfo,
)
from codexaudit.indexer.store import IndexStore
from codexaudit.indexer.symbols import (
    extract_php_runtime_dependencies,
    extract_php_symbols,
    extract_python_symbols,
    extract_ruby_symbols,
    extract_ts_symbols,
    extract_vue_symbols,
)

logger = logging.getLogger(__name__)


# Map file extensions to languages
EXTENSION_MAP: dict[str, Language] = {
    ".php": Language.PHP,
    ".py": Language.PYTHON,
    ".rb": Language.RUBY,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".vue": Language.VUE,
}

# Map languages to tree-sitter grammar names
GRAMMAR_MAP: dict[Language, str] = {
    Language.PHP: "php",
    Language.PYTHON: "python",
    Language.RUBY: "ruby",
    Language.TYPESCRIPT: "typescript",
    Language.JAVASCRIPT: "javascript",
    Language.VUE: "html",  # Vue SFCs are parsed as HTML first
}

UNSUPPORTED_SOURCE_EXTENSION_MAP: dict[str, str] = {
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".cs": "csharp",
}


def detect_language(filepath: Path) -> Language:
    """Detect the programming language from the file extension."""
    return EXTENSION_MAP.get(filepath.suffix.lower(), Language.UNKNOWN)


def discover_project_files(
    config: CodexAuditConfig,
) -> tuple[list[Path], dict[str, int]]:
    """Discover supported files plus unsupported source-language counts.

    Respects exclusion patterns from the config.
    Handles both simple directory names (e.g. 'vendor') and
    multi-segment path patterns (e.g. 'public/build').
    """
    t0 = time.monotonic()
    files: list[Path] = []
    unsupported: dict[str, int] = defaultdict(int)

    # Split exclusions into simple dir names and path-based patterns
    simple_excludes: set[str] = set()
    path_excludes: list[str] = []
    for exc in config.exclude_dirs:
        if "/" in exc:
            path_excludes.append(exc)
        else:
            simple_excludes.add(exc)

    logger.debug("Exclusions: simple=%s, path=%s", simple_excludes, path_excludes)

    for root, dirs, filenames in os.walk(config.project_path):
        rel_root = os.path.relpath(root, config.project_path)

        # Check path-based exclusions against the relative root
        skip = False
        for pexc in path_excludes:
            if rel_root == pexc or rel_root.startswith(pexc + os.sep):
                skip = True
                break
        if skip:
            logger.debug("Skipping excluded path: %s", rel_root)
            dirs.clear()
            continue

        # Filter out simple excluded directories and hidden dirs
        dirs[:] = [
            d for d in dirs if d not in simple_excludes and not d.startswith(".")
        ]

        for filename in filenames:
            filepath = Path(root) / filename
            lang = detect_language(filepath)
            if lang != Language.UNKNOWN and lang in config.languages:
                files.append(filepath)
                continue
            unsupported_language = UNSUPPORTED_SOURCE_EXTENSION_MAP.get(
                filepath.suffix.lower()
            )
            if unsupported_language:
                unsupported[unsupported_language] += 1

    elapsed = time.monotonic() - t0
    logger.info("Discovered %d source files in %.2fs", len(files), elapsed)
    return sorted(files), dict(sorted(unsupported.items()))


def discover_files(config: CodexAuditConfig) -> list[Path]:
    """Discover supported source files in the project directory."""
    files, _unsupported = discover_project_files(config)
    return files


def discover_unsupported_source_files(config: CodexAuditConfig) -> dict[str, int]:
    """Count source-like files whose languages are not currently supported."""
    _files, unsupported = discover_project_files(config)
    return unsupported


def _get_parser(language: Language):
    """Get a tree-sitter parser for the given language."""
    grammar = GRAMMAR_MAP.get(language)
    if not grammar:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        from tree_sitter_languages import get_parser

        return get_parser(grammar)


def parse_file(
    filepath: Path,
    language: Language,
    project_root: Path | None = None,
) -> tuple[list[SymbolInfo], list[DependencyInfo]]:
    """Parse a single file and extract symbols and dependencies.

    Returns (symbols, dependencies) tuple.
    """
    try:
        source_code = filepath.read_bytes()
    except (OSError, IOError) as e:
        logger.warning("Could not read file %s: %s", filepath, e)
        return [], []

    if language == Language.PYTHON:
        module_path = (
            filepath.relative_to(project_root)
            if project_root is not None
            else Path(filepath.name)
        )
        module_name, package_name = _module_names_for_python_path(module_path)
        return extract_python_symbols(
            source_code.decode("utf-8", errors="replace"),
            module_name=module_name,
            package_name=package_name,
        )

    parser = _get_parser(language)
    if parser is None:
        logger.debug("No parser available for language: %s", language)
        return [], []

    try:
        tree = parser.parse(source_code)
    except Exception as e:
        logger.warning("Parse error in %s: %s", filepath, e)
        return [], []

    root = tree.root_node

    if language == Language.PHP:
        symbols, dependencies = extract_php_symbols(root)
        dependencies.extend(
            extract_php_runtime_dependencies(
                source_code.decode("utf-8", errors="replace")
            )
        )
        return symbols, dependencies
    elif language == Language.RUBY:
        return extract_ruby_symbols(root)
    elif language in (Language.TYPESCRIPT, Language.JAVASCRIPT):
        return extract_ts_symbols(root)
    elif language == Language.VUE:
        return extract_vue_symbols(root, source_code)

    return [], []


def _module_names_for_python_path(filepath: Path) -> tuple[str, str]:
    """Return ``(module_name, package_name)`` for a Python file path."""
    parts = list(filepath.with_suffix("").parts)
    if not parts:
        return "", ""

    if parts[-1] == "__init__":
        parts = parts[:-1]
        module_name = ".".join(parts)
        return module_name, module_name

    module_name = ".".join(parts)
    package_name = ".".join(parts[:-1])
    return module_name, package_name


def index_project(config: CodexAuditConfig, store: IndexStore) -> dict:
    """Index all files in the project.

    Discovers files, parses them, extracts symbols and dependencies,
    and stores everything in SQLite.

    Returns a summary dict with counts.
    """
    t0 = time.monotonic()
    files, unsupported = discover_project_files(config)
    keep_paths = {str(filepath.relative_to(config.project_path)) for filepath in files}
    pruned = store.prune_missing_files(keep_paths)

    total_files = 0
    total_symbols = 0
    total_dependencies = 0
    skipped = 0
    errors: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[current_file]}"),
    ) as progress:
        task = progress.add_task(
            "Indexing files",
            total=len(files),
            current_file="",
        )

        for filepath in files:
            rel_path = str(filepath.relative_to(config.project_path))
            progress.update(task, current_file=rel_path)

            language = detect_language(filepath)

            # Create file record
            try:
                stat = filepath.stat()
            except OSError:
                errors.append(f"Could not stat: {rel_path}")
                progress.advance(task)
                continue

            # Incremental indexing: skip files that haven't changed
            existing = store.get_file_by_path(rel_path)
            if existing and existing.last_modified:
                try:
                    stored_mtime = (
                        datetime.fromisoformat(existing.last_modified)
                        if isinstance(existing.last_modified, str)
                        else existing.last_modified
                    )
                    if abs(stored_mtime.timestamp() - stat.st_mtime) < 1.0:
                        skipped += 1
                        logger.debug("Unchanged, skipping: %s", rel_path)
                        progress.advance(task)
                        continue
                except (ValueError, TypeError):
                    pass  # Re-index on any parsing issue

            file_info = FileInfo(
                path=rel_path,
                language=language,
                size=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
            )

            file_id = store.insert_file(file_info)
            total_files += 1

            # Parse and extract symbols/dependencies
            symbols, dependencies = parse_file(
                filepath,
                language,
                project_root=config.project_path,
            )
            logger.debug(
                "%s: %d symbols, %d dependencies",
                rel_path,
                len(symbols),
                len(dependencies),
            )

            # Set file_id on all symbols and dependencies
            for sym in symbols:
                sym.file_id = file_id
            for dep in dependencies:
                dep.source_file_id = file_id

            # Batch insert
            if symbols:
                store.insert_symbols_batch(symbols)
                total_symbols += len(symbols)

            if dependencies:
                store.insert_dependencies_batch(dependencies)
                total_dependencies += len(dependencies)

            progress.advance(task)

    elapsed = time.monotonic() - t0
    logger.info(
        "Indexing complete in %.2fs: %d files indexed, %d skipped (unchanged), "
        "%d symbols, %d dependencies, %d errors",
        elapsed,
        total_files,
        skipped,
        total_symbols,
        total_dependencies,
        len(errors),
    )

    return {
        "files_indexed": total_files,
        "files_skipped": skipped,
        "files_pruned": pruned,
        "symbols_extracted": total_symbols,
        "dependencies_found": total_dependencies,
        "unsupported_source_files": sum(unsupported.values()),
        "unsupported_language_breakdown": unsupported,
        "errors": errors,
    }
