"""SQLite storage layer for codexaudit index data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codexaudit.models import (
    ArchitecturalLayer,
    DependencyInfo,
    DependencyType,
    FileInfo,
    Language,
    SemanticEnvelope,
    SymbolInfo,
    SymbolType,
    Visibility,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    last_modified TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    namespace TEXT,
    visibility TEXT DEFAULT 'unknown',
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    parent_symbol_id INTEGER,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL,
    target_name TEXT NOT NULL,
    type TEXT NOT NULL,
    line INTEGER NOT NULL,
    FOREIGN KEY (source_file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS envelopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL UNIQUE,
    summary TEXT DEFAULT '',
    architectural_layer TEXT DEFAULT 'Unknown',
    public_api_json TEXT DEFAULT '[]',
    dependencies_intent_json TEXT DEFAULT '[]',
    side_effects_json TEXT DEFAULT '[]',
    anti_patterns_json TEXT DEFAULT '[]',
    FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_type ON symbols(type);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_dependencies_source ON dependencies(source_file_id);
CREATE INDEX IF NOT EXISTS idx_dependencies_target ON dependencies(target_name);
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
"""


class IndexStore:
    """SQLite-backed storage for the codexaudit index."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        """Create the schema if it doesn't exist."""
        self.conn.executescript(SCHEMA_SQL)

    def reset(self) -> None:
        """Drop all data and recreate schema."""
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS metrics;
            DROP TABLE IF EXISTS envelopes;
            DROP TABLE IF EXISTS dependencies;
            DROP TABLE IF EXISTS symbols;
            DROP TABLE IF EXISTS files;
        """
        )
        self.initialize()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- Files ---

    def insert_file(self, file_info: FileInfo) -> int:
        """Insert a file record and return its ID."""
        cursor = self.conn.execute(
            "INSERT OR REPLACE INTO files (path, language, size, last_modified) VALUES (?, ?, ?, ?)",
            (
                file_info.path,
                file_info.language.value,
                file_info.size,
                file_info.last_modified.isoformat()
                if file_info.last_modified
                else None,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_all_files(self) -> list[FileInfo]:
        """Get all indexed files."""
        rows = self.conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [
            FileInfo(
                id=row["id"],
                path=row["path"],
                language=Language(row["language"]),
                size=row["size"],
                last_modified=row["last_modified"],
            )
            for row in rows
        ]

    def get_file_by_path(self, path: str) -> FileInfo | None:
        """Get a file record by its path, or None if not found."""
        row = self.conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row:
            return FileInfo(
                id=row["id"],
                path=row["path"],
                language=Language(row["language"]),
                size=row["size"],
                last_modified=row["last_modified"],
            )
        return None

    def get_file_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM files").fetchone()
        return row["cnt"]

    def prune_missing_files(self, keep_paths: set[str]) -> int:
        """Delete indexed files that are no longer part of the current source set."""
        rows = self.conn.execute("SELECT path FROM files").fetchall()
        stale_paths = [row["path"] for row in rows if row["path"] not in keep_paths]
        if not stale_paths:
            return 0

        self.conn.executemany(
            "DELETE FROM files WHERE path = ?",
            [(path,) for path in stale_paths],
        )
        self.conn.commit()
        return len(stale_paths)

    def get_language_breakdown(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT language, COUNT(*) as cnt FROM files GROUP BY language ORDER BY cnt DESC"
        ).fetchall()
        return {row["language"]: row["cnt"] for row in rows}

    # --- Symbols ---

    def insert_symbol(self, symbol: SymbolInfo) -> int:
        """Insert a symbol record and return its ID."""
        cursor = self.conn.execute(
            """INSERT INTO symbols
            (file_id, type, name, namespace, visibility, line_start, line_end, parent_symbol_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol.file_id,
                symbol.type.value,
                symbol.name,
                symbol.namespace,
                symbol.visibility.value,
                symbol.line_start,
                symbol.line_end,
                symbol.parent_symbol_id,
                json.dumps(symbol.metadata),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def insert_symbols_batch(self, symbols: list[SymbolInfo]) -> None:
        """Insert symbols with proper parent-child relationships.

        Phase 1: Insert class-level symbols (class, interface, trait, enum) first
        and capture their DB IDs.
        Phase 2: Insert child symbols (methods, properties) with the correct
        parent_symbol_id linking them to their parent class.
        """
        class_types = {"class", "module", "interface", "trait", "enum"}
        class_symbols = [s for s in symbols if s.type.value in class_types]
        child_symbols = [s for s in symbols if s.type.value not in class_types]

        # Phase 1: Insert classes and track their DB IDs by name
        class_name_to_id: dict[str, int] = {}
        for s in class_symbols:
            cursor = self.conn.execute(
                """INSERT INTO symbols
                (file_id, type, name, namespace, visibility, line_start, line_end, parent_symbol_id, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s.file_id,
                    s.type.value,
                    s.name,
                    s.namespace,
                    s.visibility.value,
                    s.line_start,
                    s.line_end,
                    s.parent_symbol_id,
                    json.dumps(s.metadata),
                ),
            )
            class_name_to_id[s.name] = cursor.lastrowid  # type: ignore[assignment]

        # Phase 2: Insert methods/properties with resolved parent_symbol_id
        for s in child_symbols:
            parent_class = s.metadata.get("class")
            parent_id = class_name_to_id.get(parent_class) if parent_class else None
            self.conn.execute(
                """INSERT INTO symbols
                (file_id, type, name, namespace, visibility, line_start, line_end, parent_symbol_id, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s.file_id,
                    s.type.value,
                    s.name,
                    s.namespace,
                    s.visibility.value,
                    s.line_start,
                    s.line_end,
                    parent_id,
                    json.dumps(s.metadata),
                ),
            )

        self.conn.commit()

    def get_symbols_for_file(self, file_id: int) -> list[SymbolInfo]:
        rows = self.conn.execute(
            "SELECT * FROM symbols WHERE file_id = ? ORDER BY line_start", (file_id,)
        ).fetchall()
        return [
            SymbolInfo(
                id=row["id"],
                file_id=row["file_id"],
                type=SymbolType(row["type"]),
                name=row["name"],
                namespace=row["namespace"],
                visibility=Visibility(row["visibility"]),
                line_start=row["line_start"],
                line_end=row["line_end"],
                parent_symbol_id=row["parent_symbol_id"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def get_all_symbols(self) -> list[SymbolInfo]:
        rows = self.conn.execute(
            "SELECT * FROM symbols ORDER BY file_id, line_start"
        ).fetchall()
        return [
            SymbolInfo(
                id=row["id"],
                file_id=row["file_id"],
                type=SymbolType(row["type"]),
                name=row["name"],
                namespace=row["namespace"],
                visibility=Visibility(row["visibility"]),
                line_start=row["line_start"],
                line_end=row["line_end"],
                parent_symbol_id=row["parent_symbol_id"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def get_symbol_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM symbols").fetchone()
        return row["cnt"]

    def get_classes_with_metrics(self) -> list[dict]:
        """Get classes with method counts and line counts."""
        rows = self.conn.execute(
            """
            SELECT
                s.id, s.name, s.namespace, s.line_start, s.line_end, s.file_id,
                f.path as file_path,
                (SELECT COUNT(*) FROM symbols m WHERE m.parent_symbol_id = s.id AND m.type = 'method') as method_count,
                (s.line_end - s.line_start) as line_count
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            WHERE s.type = 'class'
            ORDER BY method_count DESC
        """
        ).fetchall()
        return [dict(row) for row in rows]

    # --- Dependencies ---

    def insert_dependency(self, dep: DependencyInfo) -> int:
        cursor = self.conn.execute(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, ?, ?)",
            (dep.source_file_id, dep.target_name, dep.type.value, dep.line),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def insert_dependencies_batch(self, deps: list[DependencyInfo]) -> None:
        self.conn.executemany(
            "INSERT INTO dependencies (source_file_id, target_name, type, line) VALUES (?, ?, ?, ?)",
            [(d.source_file_id, d.target_name, d.type.value, d.line) for d in deps],
        )
        self.conn.commit()

    def get_dependencies_for_file(self, file_id: int) -> list[DependencyInfo]:
        rows = self.conn.execute(
            "SELECT * FROM dependencies WHERE source_file_id = ? ORDER BY line",
            (file_id,),
        ).fetchall()
        return [
            DependencyInfo(
                id=row["id"],
                source_file_id=row["source_file_id"],
                target_name=row["target_name"],
                type=DependencyType(row["type"]),
                line=row["line"],
            )
            for row in rows
        ]

    def get_all_dependencies(self) -> list[DependencyInfo]:
        rows = self.conn.execute(
            "SELECT * FROM dependencies ORDER BY source_file_id, line"
        ).fetchall()
        return [
            DependencyInfo(
                id=row["id"],
                source_file_id=row["source_file_id"],
                target_name=row["target_name"],
                type=DependencyType(row["type"]),
                line=row["line"],
            )
            for row in rows
        ]

    def get_dependency_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM dependencies").fetchone()
        return row["cnt"]

    # --- Envelopes ---

    def upsert_envelope(self, envelope: SemanticEnvelope) -> int:
        cursor = self.conn.execute(
            """INSERT OR REPLACE INTO envelopes
            (file_id, summary, architectural_layer, public_api_json, dependencies_intent_json, side_effects_json, anti_patterns_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                envelope.file_id,
                envelope.summary,
                envelope.architectural_layer.value,
                json.dumps(envelope.public_api),
                json.dumps(envelope.dependencies_intent),
                json.dumps(envelope.side_effects),
                json.dumps(envelope.anti_patterns),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_all_envelopes(self) -> list[SemanticEnvelope]:
        rows = self.conn.execute("SELECT * FROM envelopes ORDER BY file_id").fetchall()
        return [
            SemanticEnvelope(
                id=row["id"],
                file_id=row["file_id"],
                summary=row["summary"],
                architectural_layer=ArchitecturalLayer(row["architectural_layer"]),
                public_api=json.loads(row["public_api_json"]),
                dependencies_intent=json.loads(row["dependencies_intent_json"]),
                side_effects=json.loads(row["side_effects_json"]),
                anti_patterns=json.loads(row["anti_patterns_json"]),
            )
            for row in rows
        ]

    def get_envelope_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM envelopes").fetchone()
        return row["cnt"]

    def get_envelopes_by_layer(self) -> dict[str, list[dict]]:
        """Get envelopes grouped by architectural layer, with file paths."""
        rows = self.conn.execute(
            """
            SELECT e.*, f.path as file_path
            FROM envelopes e
            JOIN files f ON f.id = e.file_id
            ORDER BY e.architectural_layer, f.path
        """
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for row in rows:
            layer = row["architectural_layer"]
            if layer not in result:
                result[layer] = []
            result[layer].append(
                {
                    "file_path": row["file_path"],
                    "summary": row["summary"],
                    "public_api": json.loads(row["public_api_json"]),
                    "dependencies_intent": json.loads(row["dependencies_intent_json"]),
                    "side_effects": json.loads(row["side_effects_json"]),
                    "anti_patterns": json.loads(row["anti_patterns_json"]),
                }
            )
        return result

    # --- Metrics ---

    def insert_metric(
        self, run_id: str, name: str, value: dict | list | str | int | float
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO metrics (run_id, metric_name, metric_value_json) VALUES (?, ?, ?)",
            (run_id, name, json.dumps(value)),
        )
        self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_metrics(self, run_id: str) -> dict[str, any]:
        rows = self.conn.execute(
            "SELECT metric_name, metric_value_json FROM metrics WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {
            row["metric_name"]: json.loads(row["metric_value_json"]) for row in rows
        }
