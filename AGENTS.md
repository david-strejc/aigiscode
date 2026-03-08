# AGENTS.md

> AigisCode is an AI-powered codebase evaluator. This file helps AI coding agents understand and use this project.

## What This Project Does

AigisCode analyzes entire codebases to find structural issues that single-file linters miss:
- Circular dependencies between modules (strong architectural cycles and total runtime cycles)
- Dead code (unused imports, unreferenced methods, orphaned properties, abandoned classes)
- Hardwired values (magic strings, repeated literals, hardcoded IPs and URLs, env access outside config)
- Layer violations, god classes, bottleneck files, orphan modules

It runs a six-stage pipeline: Index, Graph, Detect, Rules, AI Review, Report. Output is machine-readable JSON at `.aigiscode/aigiscode-report.json`.

## For AI Agents Using AigisCode

### Quick Start

```bash
pip install aigiscode
aigiscode analyze /path/to/project
```

The analysis report is written to `.aigiscode/aigiscode-report.json` inside the target project directory.

### Reading Results

Parse `.aigiscode/aigiscode-report.json`. Key fields:

| JSON Path | Description |
|---|---|
| `graph_analysis.strong_circular_dependencies` | Architectural cycles (high priority) |
| `graph_analysis.circular_dependencies` | All cycles including runtime/load |
| `graph_analysis.god_classes` | Classes with excessive responsibility |
| `graph_analysis.bottleneck_files` | Files with high coupling |
| `graph_analysis.orphan_files` | Files with no inbound dependencies |
| `dead_code` | Unused imports, methods, properties, classes |
| `hardwiring` | Magic strings, repeated literals, hardcoded network |
| `extensions.contract_inventory` | Routes, hooks, env vars, config keys |
| `summary.detector_coverage` | Which detectors covered which languages |

### Recommended Agent Workflow

1. Run `aigiscode analyze /repo` to generate the baseline report.
2. Parse `.aigiscode/aigiscode-report.json` for structured findings.
3. Triage findings by severity and confidence. Start with `high` confidence findings.
4. Sample findings and classify as true positive, false positive, or uncertain.
5. Apply fixes for confirmed true positives.
6. Add exclusion rules for false positives to `.aigiscode/rules.json`.
7. Encode repeated false positive patterns in `.aigiscode/policy.json`.
8. Run `aigiscode report /repo` for fast re-evaluation after policy changes.

### Policy Customization

Create `.aigiscode/policy.json` to adapt behavior per project:

```json
{
  "graph": {
    "js_import_aliases": { "@/": "src/" },
    "orphan_entry_patterns": ["src/bootstrap/**/*.ts"]
  },
  "dead_code": {
    "abandoned_entry_patterns": ["/Contracts/"]
  },
  "hardwiring": {
    "repeated_literal_min_occurrences": 4,
    "skip_path_patterns": ["app/Console/*"]
  }
}
```

See [docs/PLUGIN_SYSTEM.md](docs/PLUGIN_SYSTEM.md) for all policy fields and plugin documentation.

### What Agents Should Not Do

- Do not accept lower finding counts without sampling the new findings.
- Do not widen suppressions until the pattern is clear across multiple findings.
- Do not treat AI review as a substitute for reading the referenced code.
- Do not patch aigiscode core when policy can express the rule.

## For AI Agents Contributing to AigisCode

### Project Structure

```
src/aigiscode/
├── __init__.py             # Package version
├── __main__.py             # python -m aigiscode entry
├── cli.py                  # Typer CLI (6 commands: index, analyze, report, tune, info, plugins)
├── models.py               # Pydantic data models
├── contracts.py            # Runtime contract extraction
├── extensions.py           # Plugin loading and extension dispatch
├── filters.py              # Finding filtering
├── builtin_runtime_plugins.py  # Built-in plugin profiles (generic, django, wordpress, laravel)
├── indexer/
│   ├── parser.py           # tree-sitter + Python AST parsing
│   ├── store.py            # SQLite storage layer
│   └── symbols.py          # Symbol extraction per language
├── graph/
│   ├── builder.py          # NetworkX graph construction
│   ├── analyzer.py         # Cycles, coupling, layers, god classes
│   ├── deadcode.py         # Dead code detection
│   └── hardwiring.py       # Hardwired value detection
├── policy/
│   ├── models.py           # Policy Pydantic models
│   ├── plugins.py          # Plugin profile loading and merge
│   └── analytical.py       # AI-guided policy tuning
├── report/
│   ├── generator.py        # Markdown + JSON report generation
│   └── contracts.py        # Contract inventory for reports
├── review/
│   └── ai_reviewer.py      # AI-assisted finding classification
├── rules/
│   ├── engine.py           # Exclusion rule engine
│   └── checks.py           # Rule validation
├── ai/
│   └── backends.py         # AI backend adapters (OpenAI, Anthropic)
├── synthesis/
│   └── claude.py           # Claude-specific synthesis
└── workers/
    └── codex.py            # Codex worker integration
```

### Build and Test

```bash
# Install in development mode
uv pip install -e .

# Run tests
python -m pytest tests/ -v

# Run analysis on a project
aigiscode analyze /path/to/project

# Run without AI backends (deterministic only)
aigiscode analyze /path/to/project --skip-ai
```

### Code Style

- Python 3.12+ with full type hints on all function signatures
- Pydantic models for all data structures (see `models.py` and `policy/models.py`)
- tree-sitter for parsing PHP, TypeScript, JavaScript, Vue; Python AST for Python
- NetworkX for dependency graph construction and analysis
- Typer for CLI, Rich for terminal output
- Policy drives behavior --- do not hardcode project-specific logic into detectors

### Architecture Principles

- Generic analysis in core, project-specific behavior in policy and plugins
- Detectors emit candidates with confidence levels, not final verdicts
- AI review is final-stage classification, not first-pass detection
- Partial but explicit coverage over false certainty
- Prefer explainable heuristics over opaque model-only decisions
- Patch the analyzer core only when the issue reproduces across multiple codebases and policy cannot represent the distinction

### Key Design Boundary

An AI agent contributing to this project should prefer changing policy over changing analyzer code when:
- The false positive is project-specific
- The distinction can be expressed through an existing policy field
- The same detector is otherwise useful on other repositories

Patch aigiscode core only when policy cannot represent the distinction cleanly.
