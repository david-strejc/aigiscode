"""Hardwiring detection -- finds magic strings, hardcoded values, and tight coupling."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from aigiscode.contracts import ContractLookup, build_contract_lookup
from aigiscode.indexer.store import IndexStore
from aigiscode.policy.models import HardwiringPolicy

if TYPE_CHECKING:
    from aigiscode.extensions import ExternalPlugin

logger = logging.getLogger(__name__)
SUPPORTED_HARDWIRING_LANGUAGES = frozenset(
    {"php", "python", "ruby", "javascript", "typescript", "vue", "rust"}
)
HARDCODED_IP_URL_CATEGORY = "hardcoded_ip_url"
_PRAGMA_DATABASE_LIST = "PRAGMA database_list"
_TEST_LIKE_PATH_PARTS = frozenset({"test", "tests", "__tests__", "fixtures", "spec"})
_TEST_LIKE_PATH_SUFFIXES = (
    "test.php",
    "_test.py",
    "_test.rb",
    "_spec.rb",
)


@dataclass
class HardwiringFinding:
    """A single hardwiring finding."""

    file_path: str
    line: int
    category: str  # magic_string | repeated_literal | hardcoded_entity | hardcoded_ip_url | env_outside_config
    value: str
    context: str  # code snippet around the finding
    severity: str  # high | medium | low
    confidence: str  # high | medium | low
    suggestion: str


@dataclass
class HardwiringResult:
    """Full hardwiring analysis result."""

    magic_strings: list[HardwiringFinding] = field(default_factory=list)
    repeated_literals: list[HardwiringFinding] = field(default_factory=list)
    hardcoded_entities: list[HardwiringFinding] = field(default_factory=list)
    hardcoded_network: list[HardwiringFinding] = field(default_factory=list)
    env_outside_config: list[HardwiringFinding] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.magic_strings)
            + len(self.repeated_literals)
            + len(self.hardcoded_entities)
            + len(self.hardcoded_network)
            + len(self.env_outside_config)
        )


def _apply_finding_plugins(
    findings: list[HardwiringFinding],
    *,
    category: str,
    external_plugins: list[ExternalPlugin] | None,
    store: IndexStore,
    project_path: Path | None,
    policy: HardwiringPolicy,
    contract_lookup: ContractLookup,
) -> list[HardwiringFinding]:
    if not external_plugins or project_path is None:
        return findings

    from aigiscode.extensions import apply_hardwiring_finding_plugins

    return apply_hardwiring_finding_plugins(
        findings,
        external_plugins,
        category=category,
        store=store,
        project_path=project_path,
        policy=policy,
        contract_lookup=contract_lookup,
    )


# Strings that are framework conventions, not hardwiring
FRAMEWORK_STRINGS = frozenset(
    {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
        "required",
        "nullable",
        "string",
        "integer",
        "number",
        "object",
        "function",
        "undefined",
        "symbol",
        "bigint",
        "boolean",
        "array",
        "numeric",
        "email",
        "url",
        "min",
        "max",
        "between",
        "in",
        "exists",
        "unique",
        "confirmed",
        "date",
        "file",
        "image",
        "sometimes",
        "present",
        "filled",
        "column",
        "virtual",
        "jsonb",
        "json",
        "text",
        "varchar",
        "int",
        "float",
        "decimal",
        "bool",
        "datetime",
        "enum",
        "relation",
        "currency",
        "percent",
        "phone",
        "address",
        "true",
        "false",
        "null",
        "yes",
        "no",
        "asc",
        "desc",
        "id",
        "name",
        "type",
        "status",
        "value",
        "key",
        "label",
        "title",
        "created_at",
        "updated_at",
        "deleted_at",
        "testing",
        "production",
        "local",
        "staging",
        "development",
        "application/json",
        "text/html",
        "text/plain",
        "multipart/form-data",
        "belongsTo",
        "hasMany",
        "hasOne",
        "belongsToMany",
        "morphTo",
        "morphMany",
        "public",
        "private",
        "s3",
        "sync",
        "redis",
        "database",
    }
)

_FRAMEWORK_STRINGS_LOWER = frozenset(s.lower() for s in FRAMEWORK_STRINGS)

# -- Compiled regex patterns ------------------------------------------------

_RE_MAGIC_STRING = re.compile(
    r"""(?:===?|!==?)\s*['"]([^'"]+)['"]|['"]([^'"]+)['"]\s*(?:===?|!==?)"""
)
_RE_CASE_LABEL = re.compile(r"""case\s+['"]([^'"]+)['"]\s*:""")
_RE_COMMENT_LINE = re.compile(r"^\s*(?://|#|\*|/\*)")
_RE_STRING_LITERAL = re.compile(r"""(['"])([^'"\n]+)\1""")
_RE_IP_ADDRESS = re.compile(
    r"(?<![\d.])("
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\."
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\."
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\."
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r")(?![\d.])"
)
_RE_URL = re.compile(r"""['"]https?://[^'"]+['"]""")
_RE_ENV_CALL = re.compile(r"\benv\s*\(")
_RE_JS_ENV = re.compile(r"\b(process\.env|import\.meta\.env)\b")
_RE_JS_ENV_NAME = re.compile(r"\b(?:process\.env|import\.meta\.env)\.([A-Z0-9_]+)\b")
_RE_PY_ENV = re.compile(r"\b(?:os\.getenv|os\.environ(?:\.get)?)\s*(?:\(|\[)")
_RE_RB_ENV = re.compile(
    r"\b(?:ENV\.fetch\s*\(\s*['\"][A-Z][A-Z0-9_]*['\"]|ENV\s*\[\s*['\"][A-Z][A-Z0-9_]*['\"]\s*\])"
)
_RE_RUST_ENV = re.compile(
    r"\b(?:(?:std::)?env::(?:var|var_os)\s*\(|(?:std::)?env!\s*\(|option_env!\s*\()"
)
_RE_CONST_DEF = re.compile(r"\bconst\s+\w+\s*=")
_RE_DOCBLOCK = re.compile(r"^\s*\*")
_RE_LOCALE_CODE = re.compile(r"^[a-z]{2}(?:[_-][A-Z]{2})?$")
_RE_MIME_TYPE = re.compile(r"^[a-z][a-z0-9.+-]*/[a-z0-9.+-]+$", re.IGNORECASE)
_RE_ESCAPED_HEX_BYTES = re.compile(r"^(?:\\x[0-9A-Fa-f]{2}){2,}$")
_RE_PROTOCOL_MARKER = re.compile(r"^[+-][A-Z][A-Z0-9_-]+$")
_RE_URL_SCHEME_LITERAL = re.compile(r"^[a-z][a-z0-9+.-]*:(?://)?$", re.IGNORECASE)
_RE_HTTP_HEADER_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$", re.IGNORECASE)
_RE_CHARSET_NAME = re.compile(r"^[A-Za-z0-9._-]{3,20}$")
_RE_CHARSET_LITERAL = re.compile(
    r"^(?:utf-?\d+|utf8|iso-\d+-\d+|us-ascii|ascii|latin-?\d+)$",
    re.IGNORECASE,
)
_RE_SQL_OPERATOR = re.compile(
    r"^(?:NOT IN|NOT EXISTS|IS NULL|IS NOT NULL|BETWEEN|LIKE|IN|EXISTS|REGEXP|RLIKE)$",
    re.IGNORECASE,
)
_RE_FILENAME_LITERAL = re.compile(
    r"^[A-Za-z0-9_.-]+\.(?:php|html?|xml|json|js|css|txt|csv|svg)$",
    re.IGNORECASE,
)
_RE_SLUG_LITERAL = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$", re.IGNORECASE)
_RE_NETWORK_SINK = re.compile(
    r"\b(?:wp_(?:safe_)?remote_(?:get|post)|requests?\.(?:get|post|put|delete)|"
    r"axios\.(?:get|post|put|delete)|fetch\s*\(|httpx\.(?:get|post|put|delete)|"
    r"urllib\.request|curl_(?:init|setopt|exec)|client\.(?:get|post|put|delete)|"
    r"download|upload|send|authorize|token|callback|endpoint|base_(?:uri|url)|"
    r"api_(?:url|base)|webhook|redirect_uri)\b",
    re.IGNORECASE,
)
_RE_PROVIDER_REGISTRY = re.compile(
    r"\b(?:oembed|providers?\s*=|provider[_-]?map|registry|resolver)\b",
    re.IGNORECASE,
)
_RE_RESOURCE_HINT = re.compile(
    r"\b(?:preconnect|dns-prefetch|resource_hints?|preload|prefetch)\b",
    re.IGNORECASE,
)
_RE_DISPLAY_URL_CONTEXT = re.compile(
    r"\b(?:__\(|_e\(|esc_url\(|esc_url__\(|esc_html__\(|esc_attr__\(|help|documentation|support|learn more|read more)\b",
    re.IGNORECASE,
)
_RE_IDENTIFIER_URL_CONTEXT = re.compile(
    r"(?:<generator\b|rel\s*=\s*['\"]profile['\"]|\bprofile\b|\bxmlns\b|"
    r"\bnamespace\b|\bschema\b|\buri\b|\$links\s*\[|->link_header\b)",
    re.IGNORECASE,
)
_RE_SETTING_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_RE_HEADER_ENV_NAME = re.compile(r"^(?:HTTP|CONTENT|REMOTE|REQUEST|SERVER)_[A-Z0-9_]+$")
_RE_CODE_SYMBOL_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:\\\\][A-Za-z0-9_]+)+$")
_RE_CONTRACT_VARIABLE = re.compile(
    r"\b(?:act|action|mode|state|status|screen|slug|page|hook(?:_suffix)?|"
    r"postbox|theme_location|response(?:\.code)?|transaction_mode|"
    r"(?:[a-z]+_)?(?:id|key|name|type|format|module|backend|engine|"
    r"serializer|field|setting|option|attribute|attr|header|cookie))\b",
    re.IGNORECASE,
)
_ENTITY_CONTEXT_MARKER_PATTERN = (
    r"(?:entity(?:Type|Types|Name)?|relatedEntity(?:Type)?|rootEntityType|"
    r"folderEntityType|parsedEntityType|parent(?:_type|Type|Entity)|"
    r"target(?:_type|Type|Entity)|source(?:_type|Type|Entity)|entity_types|"
    r"in_progress_parent_type|espoRaw(?:Type|TargetType)|"
    r"(?:->|::)?getEntityType\s*\(\))"
)
_RE_ENTITY_CONTEXT_DIRECT_PREFIX = re.compile(
    rf"{_ENTITY_CONTEXT_MARKER_PATTERN}[^\n]{{0,120}}"
    rf"(?:===|!==|==|!=|=>|=|:|\?\?=|\?\?)\s*$",
    re.IGNORECASE,
)
_RE_ENTITY_CONTEXT_DIRECT_SUFFIX = re.compile(
    rf"^\s*(?:===|!==|==|!=)\s*[^\n]{{0,120}}{_ENTITY_CONTEXT_MARKER_PATTERN}",
    re.IGNORECASE,
)
_RE_TEMPLATE_DIRECTIVE = re.compile(r"^\s*(?::|@|v-[a-z])", re.IGNORECASE)
_RE_RELATIVE_IMPORT = re.compile(r"^\s*(?:\.\.?/|~?@/)")
_RE_CLI_FLAG = re.compile(r"^\s*--[a-z0-9-]+(?:=.*)?\s*$", re.IGNORECASE)
_RE_DATE_INTERVAL = re.compile(r"^\s*-\d+\s+[A-Za-z]+\s*$")
_RE_UNIT_LITERAL = re.compile(r"^[A-Za-z]{1,8}/[A-Za-z]{1,8}$")
_RE_DATA_ATTRIBUTE_NAME = re.compile(r"^data-[a-z0-9:_-]+$", re.IGNORECASE)
_RE_SEMVER_LITERAL = re.compile(
    r"^v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$",
    re.IGNORECASE,
)
_RE_CSS_UTILITY_LITERAL = re.compile(
    r"^(?:!?[a-z0-9]+(?:[:/-][a-z0-9.[\]%]+)+)$",
    re.IGNORECASE,
)
_BENIGN_IPS = frozenset({"127.0.0.1", "0.0.0.0", "255.255.255.255"})
_BENIGN_URL_PATTERNS = [
    "example.com",
    "example.org",
    "example.net",  # RFC 2606
    "schema.getpostman.com",  # Postman spec URI
    "json-schema.org",  # JSON Schema spec
    "www.w3.org",  # W3C namespace URIs
    "schemas.xmlsoap.org",  # SOAP namespace URIs
    "placeholder",  # placeholder URLs
]
_DOC_URL_HINTS = (
    "developer.",
    "docs.",
    "documentation",
    "/docs/",
    "/documentation/",
    "/support/",
    "/forum",
    "/forums/",
    "/reference/",
    "/manual/",
    "/release",
    "/releases/",
    "release-notes",
    "json-schema",
    "schema.",
    "/schema/",
    "/schemas/",
    ".xsd",
    ".dtd",
    "w3.org",
    "purl.org",
    "rssboard.org",
    "georss.org",
    "openxmlformats.org",
    "sitemaps.org",
)
_PUBLIC_PROVIDER_HOSTS = (
    "gravatar.com",
    "ui-avatars.com",
)
_PUBLIC_PROVIDER_CONTEXT = re.compile(
    r"\b(?:avatar|gravatar|fallback|provider|oembed|embed|icon|cdn|font|stylesheet)\b",
    re.IGNORECASE,
)


# -- Public API -------------------------------------------------------------


def analyze_hardwiring(
    store: IndexStore,
    min_occurrences: int = 3,
    policy: HardwiringPolicy | None = None,
    external_plugins: list[ExternalPlugin] | None = None,
    project_path: Path | None = None,
) -> HardwiringResult:
    """Run hardwiring analyses on supported indexed source files."""
    t0 = time.monotonic()
    result = HardwiringResult()
    if policy is None:
        policy = HardwiringPolicy()
    effective_min_occurrences = max(2, policy.repeated_literal_min_occurrences)
    effective_min_literal_length = max(2, policy.repeated_literal_min_length)
    effective_min_distinct_dirs = max(1, policy.repeated_literal_min_distinct_dirs)

    source_files = store.conn.execute(
        """
        SELECT id, path, language
        FROM files
        WHERE language IN ('php', 'python', 'ruby', 'javascript', 'typescript', 'vue', 'rust')
        """
    ).fetchall()

    entity_types = _get_entity_type_names(store)
    contract_lookup = build_contract_lookup(store)
    if external_plugins and project_path is not None:
        from aigiscode.extensions import apply_contract_lookup_plugins

        contract_lookup = apply_contract_lookup_plugins(
            contract_lookup,
            external_plugins,
            store=store,
            project_path=project_path,
            policy=policy,
        )
    all_string_locations: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for file_row in source_files:
        file_path: str = file_row["path"]
        language: str = file_row["language"]
        normalized_path = file_path.replace("\\", "/")

        # Skip test files — test code intentionally uses hardcoded values
        # for assertions, fixtures, and test data.
        if _is_test_like_path(normalized_path):
            continue
        if _matches_skip_patterns(normalized_path, policy.skip_path_patterns):
            continue

        content = _read_file_safe(store, file_path)
        if content is None:
            continue
        prepared_content = _prepare_content_for_analysis(file_path, content)

        magic_strings = _find_magic_strings(
            file_path,
            prepared_content,
            low_signal_literals=policy.low_signal_literals,
            min_length=policy.magic_string_min_length,
            skip_path_patterns=policy.magic_string_skip_path_patterns,
            signal_context_regexes=policy.magic_string_signal_context_regexes,
            noise_context_regexes=policy.magic_string_noise_context_regexes,
            entity_types=entity_types,
            contracts=contract_lookup,
        )
        result.magic_strings.extend(
            _apply_finding_plugins(
                magic_strings,
                category="magic_string",
                external_plugins=external_plugins,
                store=store,
                project_path=project_path,
                policy=policy,
                contract_lookup=contract_lookup,
            )
        )
        if language == "php":
            hardcoded_entities = _find_hardcoded_entities(
                file_path,
                prepared_content,
                entity_types,
                policy.entity_context_require_regexes,
                policy.entity_context_allow_regexes,
            )
            result.hardcoded_entities.extend(
                _apply_finding_plugins(
                    hardcoded_entities,
                    category="hardcoded_entity",
                    external_plugins=external_plugins,
                    store=store,
                    project_path=project_path,
                    policy=policy,
                    contract_lookup=contract_lookup,
                )
            )
        hardcoded_network = _find_hardcoded_network(file_path, prepared_content)
        result.hardcoded_network.extend(
            _apply_finding_plugins(
                hardcoded_network,
                category=HARDCODED_IP_URL_CATEGORY,
                external_plugins=external_plugins,
                store=store,
                project_path=project_path,
                policy=policy,
                contract_lookup=contract_lookup,
            )
        )
        env_outside_config = _find_env_outside_config(
            file_path,
            prepared_content,
            language=language,
            allow_js_env_names=policy.js_env_allow_names,
        )
        result.env_outside_config.extend(
            _apply_finding_plugins(
                env_outside_config,
                category="env_outside_config",
                external_plugins=external_plugins,
                store=store,
                project_path=project_path,
                policy=policy,
                contract_lookup=contract_lookup,
            )
        )
        _collect_string_literals(
            file_path,
            prepared_content,
            all_string_locations,
            min_length=effective_min_literal_length,
            low_signal_literals=policy.low_signal_literals,
            require_compound=policy.repeated_literal_require_compound,
            skip_regexes=policy.repeated_literal_skip_regexes,
            contracts=contract_lookup,
        )

    result.repeated_literals = _find_repeated_literals(
        all_string_locations,
        max(min_occurrences, effective_min_occurrences),
        min_distinct_dirs=effective_min_distinct_dirs,
    )
    result.repeated_literals = _apply_finding_plugins(
        result.repeated_literals,
        category="repeated_literal",
        external_plugins=external_plugins,
        store=store,
        project_path=project_path,
        policy=policy,
        contract_lookup=contract_lookup,
    )

    logger.info(
        "Hardwiring analysis complete: %d findings in %.2fs",
        result.total,
        time.monotonic() - t0,
    )
    return result


# -- Per-file detectors -----------------------------------------------------


def _find_magic_strings(
    file_path: str,
    content: str,
    low_signal_literals: list[str] | None = None,
    min_length: int = 5,
    skip_path_patterns: list[str] | None = None,
    signal_context_regexes: list[str] | None = None,
    noise_context_regexes: list[str] | None = None,
    entity_types: set[str] | None = None,
    contracts: ContractLookup | None = None,
) -> list[HardwiringFinding]:
    """Find magic strings used in comparisons and switch-case labels."""
    findings: list[HardwiringFinding] = []
    normalized_skip_values = {value.lower() for value in (low_signal_literals or [])}
    compiled_signal_context_regexes = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in (signal_context_regexes or [])
        if pattern
    ]
    compiled_noise_context_regexes = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in (noise_context_regexes or [])
        if pattern
    ]
    recent_code_lines: list[str] = []
    if _matches_skip_patterns(file_path, skip_path_patterns or []):
        return findings

    for lineno, line in enumerate(content.splitlines(), start=1):
        code_line = _strip_inline_comment(line)
        if _RE_COMMENT_LINE.match(code_line):
            continue
        for match in _RE_MAGIC_STRING.finditer(code_line):
            value = match.group(1) or match.group(2)
            context_text = "\n".join([*recent_code_lines[-3:], code_line])
            if _is_magic_string_noise_context(
                code_line, recent_code_lines, compiled_noise_context_regexes
            ):
                continue
            if _is_style_literal_context(code_line, value):
                continue
            if _is_protocol_or_contract_marker_context(value, context_text):
                continue
            if _requires_magic_signal_context(value) and not _has_magic_signal_context(
                code_line, recent_code_lines, compiled_signal_context_regexes
            ):
                continue
            if not _is_candidate_magic_string(
                value,
                file_path=file_path,
                min_length=min_length,
                low_signal_literals=normalized_skip_values,
                entity_types=entity_types or set(),
                contracts=contracts,
            ):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="magic_string",
                    value=value,
                    context=code_line.strip(),
                    severity="high",
                    confidence=_classify_magic_confidence(
                        value,
                        is_case_label=False,
                        has_signal_context=not _requires_magic_signal_context(value)
                        or _has_magic_signal_context(
                            code_line,
                            recent_code_lines,
                            compiled_signal_context_regexes,
                        ),
                        is_contract_like_context=_is_contract_like_magic_context(
                            file_path, context_text, value
                        ),
                    ),
                    suggestion=f"Extract '{value}' into a class constant or enum.",
                )
            )
        for match in _RE_CASE_LABEL.finditer(code_line):
            value = match.group(1)
            if _is_magic_case_noise_context(
                recent_code_lines, compiled_noise_context_regexes
            ):
                continue
            context_text = "\n".join([*recent_code_lines[-3:], code_line])
            if _is_style_literal_context(code_line, value):
                continue
            if _is_protocol_or_contract_marker_context(value, context_text):
                continue
            if _requires_magic_signal_context(value) and not _has_magic_signal_context(
                code_line, recent_code_lines, compiled_signal_context_regexes
            ):
                continue
            if not _is_candidate_magic_string(
                value,
                file_path=file_path,
                min_length=min_length,
                low_signal_literals=normalized_skip_values,
                entity_types=entity_types or set(),
                contracts=contracts,
            ):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="magic_string",
                    value=value,
                    context=code_line.strip(),
                    severity="high",
                    confidence=_classify_magic_confidence(
                        value,
                        is_case_label=True,
                        has_signal_context=not _requires_magic_signal_context(value)
                        or _has_magic_signal_context(
                            code_line,
                            recent_code_lines,
                            compiled_signal_context_regexes,
                        ),
                        is_contract_like_context=_is_contract_like_magic_context(
                            file_path, context_text, value
                        ),
                    ),
                    suggestion=f"Extract case label '{value}' into a class constant or enum.",
                )
            )
        stripped = code_line.strip()
        if stripped:
            recent_code_lines = [*recent_code_lines[-2:], stripped]
    return findings


def _find_hardcoded_entities(
    file_path: str,
    content: str,
    entity_types: set[str],
    require_context_regexes: list[str] | None = None,
    allow_context_regexes: list[str] | None = None,
) -> list[HardwiringFinding]:
    """Find hardcoded entity type names that should use class references."""
    normalized = file_path.replace("\\", "/")
    if "/Entities/" in normalized or "/i18n/" in normalized or "/lang/" in normalized:
        return []
    if _is_entity_noise_path(normalized):
        return []
    # Skip migration files — they use string names for source system mapping
    lower = normalized.lower()
    if (
        "/migration" in lower
        or "/migrate" in lower
        or "migration" in Path(normalized).stem.lower()
    ):
        return []
    # Skip espo_migration tools
    if "espo_migration" in lower:
        return []
    # Skip code generators / template stubs
    if "Make" in normalized and "Command" in normalized:
        return []

    findings: list[HardwiringFinding] = []
    compiled_require = [
        re.compile(pattern) for pattern in (require_context_regexes or []) if pattern
    ]
    compiled_allow = [
        re.compile(pattern) for pattern in (allow_context_regexes or []) if pattern
    ]
    for lineno, line in enumerate(content.splitlines(), start=1):
        code_line = _strip_inline_comment(line)
        stripped = code_line.strip()
        if _RE_COMMENT_LINE.match(code_line) or _RE_DOCBLOCK.match(code_line):
            continue
        if _RE_CONST_DEF.search(code_line):
            continue
        if any(regex.search(code_line) for regex in compiled_allow):
            continue
        if _is_display_or_prompt_context(code_line):
            continue
        # Skip SQL string context (entity names in SQL queries are DB values)
        if any(
            kw in stripped.upper()
            for kw in (
                "SELECT ",
                "INSERT ",
                "UPDATE ",
                "DELETE ",
                "FROM ",
                "JOIN ",
                "WHERE ",
            )
        ):
            continue
        string_matches = list(_RE_STRING_LITERAL.finditer(code_line))
        if len(string_matches) >= 3 and not any(
            regex.search(code_line) for regex in compiled_require
        ):
            continue
        for match in string_matches:
            value = match.group(2)
            if value not in entity_types:
                continue
            if not _is_entity_coupling_context(
                code_line,
                match.start(),
                match.end(),
                compiled_require=compiled_require,
            ):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="hardcoded_entity",
                    value=value,
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion=(
                        f"Replace hardcoded entity name '{value}' with "
                        f"{value}::getEntityType() or a domain constant."
                    ),
                )
            )
    return findings


def _find_hardcoded_network(file_path: str, content: str) -> list[HardwiringFinding]:
    """Find hardcoded IP addresses and URLs."""
    normalized = file_path.replace("\\", "/")
    if "/config/" in normalized or normalized.startswith("config/"):
        return []

    findings: list[HardwiringFinding] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if _RE_COMMENT_LINE.match(line):
            continue
        for match in _RE_IP_ADDRESS.finditer(line):
            ip = match.group(1)
            if ip in _BENIGN_IPS:
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category=HARDCODED_IP_URL_CATEGORY,
                    value=ip,
                    context=line.strip(),
                    severity="medium",
                    confidence="medium",
                    suggestion=f"Move IP address '{ip}' to configuration.",
                )
            )
        for match in _RE_URL.finditer(line):
            url = match.group(0).strip("'\"")
            # Skip benign/spec URLs
            if _is_non_runtime_url(url, line, file_path):
                continue
            # Skip URLs that are already config-wrapped defaults: config('x', 'http://...')
            if "config(" in line and "??" not in line:
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category=HARDCODED_IP_URL_CATEGORY,
                    value=url,
                    context=line.strip(),
                    severity=_classify_network_severity(url, line),
                    confidence=_classify_network_confidence(url, line),
                    suggestion="Move URL to configuration or environment variable.",
                )
            )
    return findings


def _find_env_outside_config(
    file_path: str,
    content: str,
    language: str = "php",
    allow_js_env_names: list[str] | None = None,
) -> list[HardwiringFinding]:
    """Find environment reads outside config-centric files."""
    normalized = file_path.replace("\\", "/")
    if "/config/" in normalized or normalized.startswith("config/"):
        return []
    if _is_bootstrap_config_path(normalized):
        return []
    if _is_config_or_tooling_path(normalized):
        return []
    # Skip test files — env() in tests for conditional execution is acceptable
    lower = normalized.lower()
    if _is_test_like_path(normalized):
        return []
    if "testing" in lower:
        return []

    findings: list[HardwiringFinding] = []
    allowed_js_env_names = {name.upper() for name in (allow_js_env_names or [])}
    for lineno, line in enumerate(content.splitlines(), start=1):
        if _RE_COMMENT_LINE.match(line):
            continue
        if _RE_DOCBLOCK.match(line):
            continue
        # Skip string literals that contain 'env()' — not actual calls
        # (e.g. report output: "'value' => 'env()'")
        stripped = line.strip()
        if "'env()'" in stripped or '"env()"' in stripped:
            continue
        if language == "php" and _RE_ENV_CALL.search(line):
            if _is_cli_entry_path(normalized) and not _php_env_has_default(line):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="env_outside_config",
                    value="env()",
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion="Use config() instead of env() outside config files.",
                )
            )
            continue
        if language == "python" and _RE_PY_ENV.search(line):
            if _is_cli_entry_path(normalized) and not _python_env_has_default(line):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="env_outside_config",
                    value="env",
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion=(
                        "Route environment reads through a dedicated settings/config "
                        "layer instead of direct os.environ access."
                    ),
                )
            )
            continue
        if language == "ruby" and _RE_RB_ENV.search(line):
            if _is_cli_entry_path(normalized) and not _ruby_env_has_default(line):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="env_outside_config",
                    value="env",
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion=(
                        "Route environment reads through a dedicated config object "
                        "instead of direct ENV access."
                    ),
                )
            )
            continue
        if language == "rust" and _RE_RUST_ENV.search(line):
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="env_outside_config",
                    value="env",
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion=(
                        "Route environment reads through a dedicated config layer "
                        "instead of direct std::env/env! access."
                    ),
                )
            )
            continue
        if language not in {"php", "python", "ruby"} and _RE_JS_ENV.search(line):
            env_name_match = _RE_JS_ENV_NAME.search(line)
            if (
                env_name_match
                and env_name_match.group(1).upper() in allowed_js_env_names
            ):
                continue
            if _is_cli_entry_path(normalized) and not _js_env_has_default(line):
                continue
            findings.append(
                HardwiringFinding(
                    file_path=file_path,
                    line=lineno,
                    category="env_outside_config",
                    value="env",
                    context=stripped,
                    severity="high",
                    confidence="high",
                    suggestion=(
                        "Route environment reads through a typed config module "
                        "instead of direct process/import.meta access."
                    ),
                )
            )
    return findings


# -- Cross-file analysis ---------------------------------------------------


def _collect_string_literals(
    file_path: str,
    content: str,
    collector: dict[str, list[tuple[str, int]]],
    min_length: int = 4,
    low_signal_literals: list[str] | None = None,
    require_compound: bool = True,
    skip_regexes: list[str] | None = None,
    contracts: ContractLookup | None = None,
) -> None:
    """Extract string literals from a file into the cross-file collector."""
    normalized_skip_values = {value.lower() for value in (low_signal_literals or [])}
    compiled_skip_regexes = [re.compile(pattern) for pattern in (skip_regexes or [])]
    for lineno, line in enumerate(content.splitlines(), start=1):
        code_line = _strip_inline_comment(line)
        if _RE_COMMENT_LINE.match(code_line):
            continue
        if _RE_DOCBLOCK.match(code_line):
            continue
        for match in _RE_STRING_LITERAL.finditer(code_line):
            value = match.group(2)
            if _is_style_literal_context(code_line, value):
                continue
            if not _is_candidate_repeated_literal(
                value,
                min_length=min_length,
                low_signal_literals=normalized_skip_values,
                require_compound=require_compound,
                compiled_skip_regexes=compiled_skip_regexes,
            ):
                continue
            if _is_declared_contract_literal(value, contracts):
                continue
            collector[value].append((file_path, lineno))


def _find_repeated_literals(
    collector: dict[str, list[tuple[str, int]]],
    min_occurrences: int,
    min_distinct_dirs: int = 1,
) -> list[HardwiringFinding]:
    """Find string literals repeated across min_occurrences or more distinct files."""
    findings: list[HardwiringFinding] = []
    for value, locations in sorted(collector.items()):
        distinct_files = {fp for fp, _ in locations}
        if len(distinct_files) < min_occurrences:
            continue
        distinct_dirs = {str(Path(fp).parent) for fp in distinct_files}
        if len(distinct_dirs) < min_distinct_dirs:
            continue

        sample = locations[:5]
        detail = ", ".join(f"{fp}:{ln}" for fp, ln in sample)
        if len(locations) > 5:
            detail += f" ... and {len(locations) - 5} more"

        findings.append(
            HardwiringFinding(
                file_path=locations[0][0],
                line=locations[0][1],
                category="repeated_literal",
                value=value,
                context=f"Found in {len(distinct_files)} files: {detail}",
                severity="medium",
                confidence=_classify_repeated_literal_confidence(
                    value=value,
                    file_count=len(distinct_files),
                    dir_count=len(distinct_dirs),
                ),
                suggestion=(
                    f"Extract repeated literal '{value}' into a shared constant "
                    f"(appears in {len(distinct_files)} files)."
                ),
            )
        )
    return findings


# -- Helpers ----------------------------------------------------------------


def _get_entity_type_names(store: IndexStore) -> set[str]:
    """Get all class names defined under Entities/ directories."""
    rows = store.conn.execute("""
        SELECT DISTINCT s.name FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.type = 'class' AND f.path LIKE '%/Entities/%'
    """).fetchall()
    return {row["name"] for row in rows}


def _read_file_safe(store: IndexStore, relative_path: str) -> str | None:
    """Read a file from the project root, returning None on any failure."""
    db_path = Path(store.conn.execute(_PRAGMA_DATABASE_LIST).fetchone()["file"])
    project_root = db_path.parent.parent
    full_path = project_root / relative_path
    if not full_path.exists():
        return None
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _prepare_content_for_analysis(file_path: str, content: str) -> str:
    """Remove non-code regions that create syntax-only noise."""
    if not file_path.endswith(".vue"):
        return content

    prepared_lines: list[str] = []
    in_script = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("<script"):
            in_script = True
            prepared_lines.append("")
            continue
        if stripped.startswith("</script"):
            in_script = False
            prepared_lines.append("")
            continue
        prepared_lines.append(line if in_script else "")
    return "\n".join(prepared_lines)


def _strip_inline_comment(line: str) -> str:
    """Remove trailing line comments while preserving quoted content."""
    quote: str | None = None
    escape = False
    chars: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if quote is not None:
            chars.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            chars.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/" and (i == 0 or line[i - 1] != ":"):
            break
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        chars.append(ch)
        i += 1
    return "".join(chars)


def _is_ignorable_string(value: str) -> bool:
    """Return True if the string is a framework convention or too short."""
    if not value or len(value) <= 1:
        return True
    if value.lower() in _FRAMEWORK_STRINGS_LOWER:
        return True
    # Whitespace-only strings
    if not value.strip():
        return True
    # File extensions (.php, .js, .ts, etc.)
    if re.match(r"^\.\w{1,5}$", value):
        return True
    # Single words that are common identifiers (snake_case or camelCase, <= 15 chars)
    if re.match(r"^[a-z][a-z0-9_]{0,14}$", value) and "_" in value:
        return True
    # Regex replacement patterns ($1, $2, etc.)
    if re.match(r"^[\s$\\]+\d*$", value):
        return True
    return False


def _is_test_like_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    stem = Path(normalized).stem.lower()
    parts = [part for part in normalized.split("/") if part]
    return (
        "test" in stem
        or any(part in _TEST_LIKE_PATH_PARTS for part in parts)
        or normalized.endswith(_TEST_LIKE_PATH_SUFFIXES)
    )


def _is_build_tooling_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    filename = Path(normalized).name.lower()
    tooling_filenames = {
        "gruntfile.js",
        "gulpfile.js",
        "webpack.config.js",
        "rollup.config.js",
        "vite.config.js",
        "vite.config.ts",
        "eslint.config.js",
        "rakefile",
        "build.rs",
    }
    return filename in tooling_filenames or normalized.startswith("tools/")


def _is_config_or_tooling_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    filename = Path(normalized).name.lower()
    config_like = {
        "playwright.config.js",
        "playwright.config.ts",
        "jest.config.js",
        "jest.config.ts",
        "phpunit.xml",
        "phpunit.xml.dist",
        "gemfile",
    }
    return (
        normalized.startswith(("tools/", "scripts/", "config/"))
        or _is_build_tooling_path(normalized)
        or filename.endswith(".gemspec")
        or filename in config_like
    )


def _is_bootstrap_config_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    filename = Path(normalized).name.lower()
    parts = [part for part in normalized.split("/") if part]
    return (
        normalized.startswith(("bootstrap/",))
        or filename in {"manage.py", "settings.py", "settings.py-tpl", "config.ru"}
        or any(part in {"bootstrap", "conf", "settings"} for part in parts)
    )


def _is_cli_entry_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    filename = Path(normalized).name.lower()
    return (
        filename == "manage.py"
        or normalized.startswith(("bin/", "scripts/"))
        or any(
            part in {"management", "commands", "console", "cli", "command"}
            for part in parts
        )
    )


def _php_env_has_default(line: str) -> bool:
    return bool(re.search(r"\benv\s*\(\s*[^,]+,\s*.+\)", line))


def _python_env_has_default(line: str) -> bool:
    return bool(re.search(r"\bos\.(?:getenv|environ\.get)\s*\(\s*[^,]+,\s*.+\)", line))


def _ruby_env_has_default(line: str) -> bool:
    return bool(re.search(r"\bENV\.fetch\s*\(\s*[^,]+,\s*.+\)", line))


def _js_env_has_default(line: str) -> bool:
    return bool(
        re.search(
            r"\b(?:process\.env|import\.meta\.env)(?:\.[A-Z0-9_]+|\s*\[\s*['\"][A-Z0-9_]+['\"]\s*\])\s*(?:\|\||\?\?)",
            line,
        )
    )


def _is_selector_like_literal(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith(("#", ".", "[")) and any(
        ch.isalpha() for ch in normalized
    )


def _is_html_data_attribute_name(value: str) -> bool:
    return _RE_DATA_ATTRIBUTE_NAME.fullmatch(value.strip()) is not None


def _looks_like_css_utility_literal(value: str) -> bool:
    normalized = value.strip()
    if " " in normalized or normalized.count("#") > 1:
        return False
    if _RE_CSS_UTILITY_LITERAL.fullmatch(normalized) is None:
        return False
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "bg-",
            "text-",
            "opacity-",
            "border-",
            "shadow",
            "rounded",
            "animate",
            "cursor-",
            "translate-",
            "-translate-",
            "scale-",
            "rotate-",
            "inset-",
            "top-",
            "right-",
            "bottom-",
            "left-",
            "mt-",
            "mb-",
            "ml-",
            "mr-",
            "mx-",
            "my-",
            "pt-",
            "pb-",
            "pl-",
            "pr-",
            "px-",
            "py-",
            "p-",
            "w-",
            "h-",
            "min-",
            "max-",
            "flex-",
            "grid-",
            "font-",
            "leading-",
            "tracking-",
            "z-",
        )
    )


def _is_style_literal_context(code_line: str, value: str) -> bool:
    normalized = value.strip()
    lower_line = code_line.lower()
    if _is_html_data_attribute_name(normalized):
        return True
    if not _looks_like_css_utility_literal(normalized):
        return False
    return bool(
        re.search(
            r"\bclass(?:name|list)?\b|class\s*=|class:|add_breadcrumb|dataset\.|setattribute\s*\(\s*['\"]class['\"]",
            lower_line,
        )
    )


def _is_simple_symbolic_token(value: str) -> bool:
    return bool(re.fullmatch(r"_?[A-Za-z][A-Za-z0-9_]*", value))


def _has_compound_marker(value: str) -> bool:
    return bool(re.search(r"[._:/-]", value))


def _looks_like_fragment(value: str) -> bool:
    if any(token in value for token in ("&&", "||", "==", "!=", "=>", "->", "::")):
        return True
    if any(ch in value for ch in ("{", "}", "$", "<", ">", "[", "]", "(", ")")):
        return True
    stripped = value.strip()
    if not stripped:
        return True
    alnum_count = sum(ch.isalnum() for ch in stripped)
    if alnum_count == 0:
        return True
    if alnum_count / max(len(stripped), 1) < 0.55:
        return True
    return False


def _is_route_literal(value: str) -> bool:
    return value.startswith("/api/") or value.startswith("/admin/")


def _is_environment_path_literal(value: str) -> bool:
    return value.startswith(("/home/", "/var/", "/srv/", "/opt/", "/etc/"))


def _looks_like_pathish_literal(value: str) -> bool:
    normalized = value.strip()
    if _RE_RELATIVE_IMPORT.match(normalized):
        return True
    if normalized.startswith("/") and not (
        _is_route_literal(normalized) or _is_environment_path_literal(normalized)
    ):
        return True
    return bool(re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", normalized))


def _matches_compiled_regexes(value: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _is_declared_contract_literal(
    value: str,
    contracts: ContractLookup | None,
) -> bool:
    if contracts is None:
        return False
    normalized = value.strip()
    return (
        normalized in contracts.routes
        or normalized in contracts.hooks
        or normalized in contracts.registered_keys
        or normalized in contracts.symbolic_literals
        or normalized in contracts.env_keys
        or normalized in contracts.config_keys
    )


def _is_protocol_literal(value: str) -> bool:
    normalized = value.strip()
    return (
        _RE_MIME_TYPE.fullmatch(normalized) is not None
        or _RE_ESCAPED_HEX_BYTES.fullmatch(normalized) is not None
        or _RE_PROTOCOL_MARKER.fullmatch(normalized) is not None
        or _RE_CHARSET_LITERAL.fullmatch(normalized) is not None
        or normalized == ":memory:"
    )


def _is_protocol_or_contract_marker_context(value: str, code_line: str) -> bool:
    normalized = value.strip()
    lower_value = normalized.lower()
    lower_line = code_line.lower()
    if _RE_SETTING_NAME.fullmatch(normalized) is not None and (
        re.search(
            r"\b(setting|settings|environment_variable|settings_module|configure)\b",
            lower_line,
        )
        or _RE_CONTRACT_VARIABLE.search(lower_line)
    ):
        return True
    if (
        _RE_HEADER_ENV_NAME.fullmatch(normalized) is not None
        and re.search(
            r"\b(header|headers|meta|cookie|request|response|scope|environ|server)\b",
            lower_line,
        )
        or (
            _RE_HEADER_ENV_NAME.fullmatch(normalized) is not None
            and _RE_CONTRACT_VARIABLE.search(lower_line)
        )
    ):
        return True
    if _RE_CODE_SYMBOL_PATH.fullmatch(normalized) is not None and re.search(
        r"\b(module|import|serializer|path|backend|engine|class|field|app|package|check|error|id)\b",
        lower_line,
    ):
        return True
    if _RE_SQL_OPERATOR.fullmatch(normalized) is not None and re.search(
        r"\b(operator|compare|comparator|sql)\b",
        lower_line,
    ):
        return True
    if (
        lower_value in {"http://", "https://", "http:", "https:", "javascript:"}
        or _RE_URL_SCHEME_LITERAL.fullmatch(normalized) is not None
    ) and re.search(
        r"\b(url|href|src|scheme|protocol|location|redirect)\b", lower_line
    ):
        return True
    if _RE_HTTP_HEADER_NAME.fullmatch(normalized) is not None and re.search(
        r"\b(header|headers|charset|encoding|mime|accept|content[_-])",
        lower_line,
    ):
        return True
    if _RE_CHARSET_NAME.fullmatch(normalized) is not None and re.search(
        r"\b(charset|encoding|codepage|mbstring|iconv)\b",
        lower_line,
    ):
        return True
    if (
        _RE_FILENAME_LITERAL.fullmatch(normalized) is not None
        or _RE_SLUG_LITERAL.fullmatch(normalized) is not None
    ) and re.search(
        r"\b(pagenow|page_now|hook_suffix|current_screen|menu_page|page_hook)\b|screen(?:->|\.)",
        lower_line,
    ):
        return True
    if (
        _RE_FILENAME_LITERAL.fullmatch(normalized) is not None
        or _RE_SLUG_LITERAL.fullmatch(normalized) is not None
    ) and _RE_CONTRACT_VARIABLE.search(lower_line):
        return True
    return False


def _current_literal_segment(prefix: str) -> str:
    """Return the current comma-separated segment leading into a literal."""
    last_delimiter = max(prefix.rfind(","), prefix.rfind(";"), prefix.rfind("{"))
    return prefix[last_delimiter + 1 :]


def _is_entity_coupling_context(
    code_line: str,
    start: int,
    end: int,
    compiled_require: list[re.Pattern[str]],
) -> bool:
    prefix = code_line[max(0, start - 160) : start]
    suffix = code_line[end : min(len(code_line), end + 120)]
    segment = _current_literal_segment(prefix)
    if _RE_ENTITY_CONTEXT_DIRECT_PREFIX.search(segment):
        return True
    if _RE_ENTITY_CONTEXT_DIRECT_SUFFIX.search(suffix):
        return True
    return any(regex.search(code_line) for regex in compiled_require)


def _is_entity_noise_path(path: str) -> bool:
    lower = path.lower()
    stem = Path(path).stem.lower()
    return (
        lower.startswith("scripts/")
        or "/scripts/" in lower
        or "/database/seeders/" in lower
        or lower.endswith("/module.php")
        or "seeder" in stem
        or "generator" in stem
        or "translation" in stem
        or "mockdata" in stem
    )


def _is_display_or_prompt_context(code_line: str) -> bool:
    lower = code_line.lower()
    if "inertia::render(" in lower or "getlabel(" in lower or "geticon(" in lower:
        return True
    if any(
        token in lower
        for token in (
            "'icon'",
            '"icon"',
            "'label'",
            '"label"',
            "'labelkey'",
            '"labelkey"',
            "'title'",
            '"title"',
            "'description'",
            '"description"',
            "'tooltip'",
            '"tooltip"',
            "'placeholder'",
            '"placeholder"',
        )
    ):
        return True
    return bool(
        re.search(
            r"['\"](?:prompt|instructions|examples?)['\"]\s*=>",
            code_line,
            re.IGNORECASE,
        )
    )


def _is_magic_string_noise_context(
    code_line: str,
    recent_code_lines: list[str],
    compiled_noise_context_regexes: list[re.Pattern[str]],
) -> bool:
    lower = code_line.lower()
    if _matches_compiled_regexes(code_line, compiled_noise_context_regexes):
        return True
    if any(
        marker in lower
        for marker in (
            "['options']",
            '["options"]',
            "['relation']",
            '["relation"]',
            "['type']",
            '["type"]',
            "['storage']",
            '["storage"]',
            "['panel']",
            '["panel"]',
            "['key']",
            '["key"]',
        )
    ):
        return True
    if re.search(
        r"\$(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|"
        r"column(?:name)?|key|normalizedField)\b\s*(?:===|!==|==|!=)",
        code_line,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"(?:===|!==|==|!=)\s*\$(?:field(?:type|name)?|definition|relation|"
        r"options?|panel(?:name)?|column(?:name)?|key|normalizedField)\b",
        code_line,
        re.IGNORECASE,
    ):
        return True
    return _is_magic_case_noise_context(
        recent_code_lines, compiled_noise_context_regexes
    )


def _is_magic_case_noise_context(
    recent_code_lines: list[str],
    compiled_noise_context_regexes: list[re.Pattern[str]],
) -> bool:
    context = "\n".join(recent_code_lines[-3:])
    if _matches_compiled_regexes(context, compiled_noise_context_regexes):
        return True
    return bool(
        re.search(
            r"\b(?:switch|match)\s*\([^)]*"
            r"(?:field(?:type|name)?|definition|relation|options?|panel(?:name)?|"
            r"column(?:name)?|key|cap(?:ability)?|permission|headername|"
            r"response\.code|theme_location)\b",
            context,
            re.IGNORECASE,
        )
    )


def _requires_magic_signal_context(value: str) -> bool:
    normalized = value.strip()
    return _is_simple_symbolic_token(normalized) and not _has_compound_marker(
        normalized
    )


def _has_magic_signal_context(
    code_line: str,
    recent_code_lines: list[str],
    compiled_signal_context_regexes: list[re.Pattern[str]],
) -> bool:
    if not compiled_signal_context_regexes:
        return True
    if _matches_compiled_regexes(code_line, compiled_signal_context_regexes):
        return True
    context = "\n".join([*recent_code_lines[-3:], code_line])
    return _matches_compiled_regexes(context, compiled_signal_context_regexes)


def _classify_magic_confidence(
    value: str,
    is_case_label: bool,
    has_signal_context: bool,
    is_contract_like_context: bool,
) -> str:
    normalized = value.strip()
    if is_contract_like_context:
        return "medium" if is_case_label or _has_compound_marker(normalized) else "low"
    if is_case_label or _has_compound_marker(normalized):
        return "high"
    if has_signal_context:
        return "medium"
    return "low"


def _is_contract_like_magic_context(
    file_path: str,
    context_text: str,
    value: str,
) -> bool:
    lower_context = context_text.lower()
    normalized = value.strip()
    if _RE_CONTRACT_VARIABLE.search(lower_context) and _RE_CODE_SYMBOL_PATH.fullmatch(
        normalized
    ):
        return True
    if _is_interactive_path(file_path) and _RE_SLUG_LITERAL.fullmatch(normalized):
        if re.search(
            r"\b(act|action|mode|state|screen|slug|keystr|postbox|response\.code|"
            r"theme_location|pagenow|hook_suffix|id|page|format|view|tab)\b",
            lower_context,
        ):
            return True
    return False


def _classify_repeated_literal_confidence(
    value: str,
    file_count: int,
    dir_count: int,
) -> str:
    normalized = value.strip()
    if _is_route_literal(normalized) or _is_environment_path_literal(normalized):
        return "high"
    if normalized.startswith(".") and file_count >= 3 and dir_count >= 2:
        return "medium"
    if _has_compound_marker(normalized) and file_count >= 4 and dir_count >= 3:
        return "medium"
    return "low"


def _classify_network_confidence(url: str, code_line: str) -> str:
    lower_line = code_line.lower()
    host = urlparse(url).netloc.lower()
    if _RE_NETWORK_SINK.search(code_line):
        return "high"
    if _RE_PROVIDER_REGISTRY.search(code_line) or _RE_RESOURCE_HINT.search(code_line):
        return "low"
    if host.endswith(".local") or "localhost" in host or "{" in url or "$" in url:
        return "high"
    if any(token in lower_line for token in ("href", "src", "origin", "asset", "font")):
        return "low"
    return "medium"


def _classify_network_severity(url: str, code_line: str) -> str:
    confidence = _classify_network_confidence(url, code_line)
    if confidence == "high":
        return "high"
    return "medium"


def _is_interactive_path(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/")
    return any(
        marker in normalized
        for marker in (
            "/Console/",
            "/resources/js/",
            "/src/js/",
            "/frontend/",
            "/assets/",
            "/Components/",
            "/Pages/",
            "/composables/",
            "/stores/",
        )
    )


def _is_candidate_magic_string(
    value: str,
    file_path: str,
    min_length: int,
    low_signal_literals: set[str],
    entity_types: set[str],
    contracts: ContractLookup | None = None,
) -> bool:
    normalized = value.strip()
    if (
        len(normalized) < min_length
        or normalized.isdigit()
        or _is_ignorable_string(normalized)
        or normalized.lower() in low_signal_literals
        or _is_html_data_attribute_name(normalized)
        or _looks_like_css_utility_literal(normalized)
        or _RE_SEMVER_LITERAL.fullmatch(normalized) is not None
        or _looks_like_fragment(normalized)
        or normalized in entity_types
        or _RE_IP_ADDRESS.fullmatch(normalized) is not None
        or _RE_LOCALE_CODE.fullmatch(normalized) is not None
        or normalized.startswith("_")
        or _is_declared_contract_literal(normalized, contracts)
        or _is_protocol_literal(normalized)
    ):
        return False
    if _is_selector_like_literal(normalized):
        return False
    if _is_build_tooling_path(file_path) and re.fullmatch(
        r"[a-z0-9_-]+(?::[a-z0-9_-]+)?",
        normalized,
    ):
        return False
    if normalized.lower() in {"http", "https", "localhost", "xmlhttprequest", "utf-8"}:
        return False
    if _is_interactive_path(file_path) and _is_simple_symbolic_token(normalized):
        return False
    return True


def _is_candidate_repeated_literal(
    value: str,
    min_length: int,
    low_signal_literals: set[str],
    require_compound: bool,
    compiled_skip_regexes: list[re.Pattern[str]],
) -> bool:
    normalized = value.strip()
    if (
        len(normalized) < min_length
        or normalized.isdigit()
        or _is_ignorable_string(normalized)
        or normalized.lower() in low_signal_literals
        or _is_html_data_attribute_name(normalized)
        or _looks_like_css_utility_literal(normalized)
        or _RE_SEMVER_LITERAL.fullmatch(normalized) is not None
        or _looks_like_fragment(normalized)
        or _matches_compiled_regexes(normalized, compiled_skip_regexes)
        or _looks_like_pathish_literal(normalized)
        or _RE_UNIT_LITERAL.fullmatch(normalized) is not None
        or _RE_DATE_INTERVAL.fullmatch(normalized) is not None
        or _is_protocol_literal(normalized)
    ):
        if _is_route_literal(normalized) or _is_environment_path_literal(normalized):
            return True
        return False
    if require_compound and not _has_compound_marker(normalized):
        return False
    if _RE_TEMPLATE_DIRECTIVE.match(normalized):
        return False
    return True


def _matches_skip_patterns(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch(normalized, pattern) for pattern in patterns if pattern)


def _is_non_runtime_url(url: str, code_line: str, file_path: str = "") -> bool:
    lower_url = url.lower()
    lower_line = code_line.lower()
    lower_path = file_path.replace("\\", "/").lower()
    if any(pattern in lower_url for pattern in _BENIGN_URL_PATTERNS):
        return True
    if any(hint in lower_url for hint in _DOC_URL_HINTS):
        return True
    host = urlparse(url).netloc.lower()
    if any(host.endswith(public_host) for public_host in _PUBLIC_PROVIDER_HOSTS) and (
        _PUBLIC_PROVIDER_CONTEXT.search(code_line)
        or any(token in lower_path for token in ("avatar", "gravatar", "embed"))
    ):
        return True
    if _RE_IDENTIFIER_URL_CONTEXT.search(code_line):
        return True
    if _RE_DISPLAY_URL_CONTEXT.search(code_line) and not _RE_NETWORK_SINK.search(
        code_line
    ):
        return True
    if "<a href=" in lower_line and (
        any(
            token in lower_line
            for token in ("__(", "_e(", "esc_html__(", "esc_attr__(", "help")
        )
        or any(
            phrase in lower_line
            for phrase in ("documentation", "support", "learn more", "read more")
        )
    ):
        return True
    return False
