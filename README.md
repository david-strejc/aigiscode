<p align="center">
  <br />
  <strong>AigisCode</strong>
  <br />
  <em>Static analysis built for AI agents. Whole-codebase evaluation at scale.</em>
  <br />
  <br />
</p>

<p align="center">
  <a href="https://pypi.org/project/aigiscode/"><img src="https://img.shields.io/pypi/v/aigiscode?color=blue&label=PyPI" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/aigiscode/"><img src="https://img.shields.io/pypi/pyversions/aigiscode" alt="Python 3.12+" /></a>
  <a href="https://github.com/Draivix/aigiscode/actions"><img src="https://img.shields.io/github/actions/workflow/status/Draivix/aigiscode/ci.yml?label=CI" alt="CI status" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" /></a>
</p>

---

> **Built by AI. Used by AI. Understood by AI.**
>
> AigisCode is evaluation infrastructure for AI coding agents. It analyzes entire
> codebases to find the structural problems that single-file linters miss ---
> circular dependencies, dead code, hardwired values, layer violations, and
> architectural bottlenecks. The output is machine-readable JSON designed for
> AI agents to parse, triage, and act on. Humans benefit from the reports, but
> the primary workflow is: **AI agent runs AigisCode, AI agent reads JSON,
> AI agent fixes code.**

**Aigis** (Greek: Aigis) is the ancient Greek word for _Aegis_ --- the divine shield.
The first two letters happen to be **AI**. AigisCode is your codebase's shield
against architectural decay.

## Quick Start

```bash
pip install aigiscode
cd your-project
aigiscode analyze .
```

AigisCode indexes your source, builds a dependency graph, runs detectors,
applies policy rules, optionally asks an AI backend to review the results,
and generates both human-readable Markdown and machine-readable JSON reports.

On Python, AigisCode does not use the TypeScript Codex SDK directly. The local
runtime uses either an authenticated `codex` CLI session or the OpenAI
Responses API, depending on configured backend order.

The machine-readable report is at:

```
.aigiscode/aigiscode-report.json
```

Each run also writes a resumable agent handoff artifact at:

```
.aigiscode/aigiscode-handoff.json
.aigiscode/aigiscode-handoff.md
```

Each run also writes timestamped archives under:

```
.aigiscode/reports/
```

If you want agents to write into a dedicated folder such as `reports/aigiscode`,
use `--output-dir reports/aigiscode`.

## Real-World Evaluation Results

AigisCode has been tested on major open-source codebases. These are real numbers
from production runs, not synthetic benchmarks.

| Project | Files | Symbols | Dependencies | Circular Deps | God Classes | Dead Code | Hardwiring |
|---|---|---|---|---|---|---|---|
| **Django** | 2,929 | 46,064 | 25,311 | 100 | 102 | 151 | 135 |
| **WordPress** | 3,340 | 33,804 | 6,791 | 9 strong / 64 total | 150 | 120 | 2,221 |
| **Spina** (Rails) | 292 | 1,247 | 968 | 1 | 4 | minimal | minimal |
| **Newerp** (Laravel+Vue) | 5,363 | 27,302 | 21,055 | 22 | 289 | 428 | 1,293 |

Independent validation on Newerp: approximately 50% dead code precision, layer
violations confirmed actionable by human reviewers. These results demonstrate
that AigisCode scales from small Rails gems to large multi-language monoliths.

## What Does AigisCode Find?

| Category | Examples |
|---|---|
| **Circular dependencies** | Module A imports B, B imports C, C imports A --- both strong (architectural) and total (runtime/load) cycles |
| **Dead code** | Unused imports, unreferenced private methods, orphaned properties, abandoned classes |
| **Hardwired values** | Magic strings, repeated literals, hardcoded IPs and URLs, env access outside config |
| **Layer violations** | A controller importing directly from a view, a model reaching into middleware |
| **Structural risks** | God classes, bottleneck files, orphan modules with no inbound dependencies |
| **Runtime contracts** | Routes, hooks, env vars, config keys --- extracted and cross-referenced against findings |

Every finding includes file path, line number, category, confidence level, and a
suggested fix. False positive rates are driven down by contract-aware filtering
and optional AI review.

## How It Works

AigisCode runs a six-stage pipeline:

```
 Source Code
     |
     v
 +---------+     +----------+     +----------+     +---------+     +-----------+     +----------+
 |  Index  | --> |  Graph   | --> |  Detect  | --> |  Rules  | --> | AI Review | --> |  Report  |
 +---------+     +----------+     +----------+     +---------+     +-----------+     +----------+
  tree-sitter     dependency       dead code        saved rules     classify          JSON + MD
  Python AST      analysis         hardwiring       pre-filter      true_positive     contract
  SQLite store    cycles,          magic strings    false           false_positive    inventory
  symbols,        coupling,                         positives       needs_context     metrics
  dependencies    layers
```

**1. Index** --- Parses source files with tree-sitter (PHP, TypeScript, JavaScript, Vue) and Python AST. Stores files, symbols, dependencies, and semantic envelopes in a local SQLite database. Supports incremental re-indexing.

**2. Graph** --- Builds a file-level dependency graph with NetworkX. Computes circular dependencies (strong vs. total), coupling metrics, bottleneck files, layer violations, god classes, orphan files, and runtime entry candidates.

**3. Detect** --- Runs generic detector passes for dead code and hardwiring. Detectors emit candidates with confidence levels; they do not encode project-specific logic.

**4. Rules** --- Applies saved exclusion rules from `.aigiscode/rules.json` to pre-filter known false positives. Rules are the durable memory of prior audits.

**5. AI Review** --- Sends a sample of remaining findings to an AI backend (OpenAI Codex or Anthropic Claude) for classification as `true_positive`, `false_positive`, or `needs_context`. Proposes new exclusion rules from confirmed false positives.

**6. Report** --- Generates a structured JSON report and a human-readable Markdown summary. Includes a contract inventory (routes, hooks, env keys, config keys) and full metric breakdowns.

When external analyzers are enabled, imported `domain=security` findings also flow through a dedicated AI security review step during `analyze`. That review emits verdicts under `security_review`, can generate durable exclusion rules, and is folded into the same feedback-loop accounting.

The report also exposes a first-class **self-healing feedback loop** summary:
- actionable visible findings that still need work
- accepted / suppressed findings already encoded in rules or policy
- informational findings that should not page humans like real defects
- imported external findings that must converge into the same triage lifecycle
- AI review counts and whether the next run should be quieter because new rules were learned

Every run also writes a backend-neutral **agent handoff artifact** alongside the full report:
- `aigiscode-handoff.json` for the next agent/tool session
- `aigiscode-handoff.md` for human-readable resume context
- archived copies under `.aigiscode/reports/<run-id>/`
- structured priorities, accepted noise, needs-context items, verification commands, and coverage warnings

Imported external findings follow the same high-level contract:
- raw scanner artifacts are preserved under `.aigiscode/reports/<run-id>/raw/`
- AigisCode normalizes them into `external_analysis`
- saved rules can pre-filter repeated false positives before any review happens
- `analyze` can AI-review imported security findings as the final triage step
- `report` stays fast and deterministic: it re-runs normalization and rules, but does not perform a fresh AI review

## Supported Languages

| Language | Index | Dead Code Detection | Hardwiring Detection | Parser |
|---|:---:|:---:|:---:|---|
| PHP | yes | yes | yes | tree-sitter |
| Python | yes | yes | yes | Python AST |
| TypeScript | yes | yes | yes | tree-sitter |
| JavaScript | yes | yes | yes | tree-sitter |
| Vue | yes | yes | yes | tree-sitter |
| Ruby | yes | -- | yes | tree-sitter |
| Rust | yes | yes | yes | tree-sitter |

Detector coverage is reported explicitly. When a language is indexed but a
detector does not yet support it, the report flags partial coverage instead of
silently treating it as fully analyzed.

## CLI Commands

```
aigiscode index <path>        Parse and store the codebase index
aigiscode analyze <path>      Full pipeline: index + graph + detect + review + report
aigiscode report <path>       Re-generate report from existing index (fast re-evaluation)
aigiscode tune <path>         AI-guided policy tuning with regression guards
aigiscode info <path>         Show index stats and detector coverage
aigiscode plugins             List available plugins and their policy fields
```

Key flags:

```
--skip-ai                     Run without AI backends (deterministic only)
--analytical-mode             Ask AI to propose a policy patch
--reset                       Full re-index (ignore incremental cache)
--output-dir <path>          Store the DB, rules, policies, and reports outside `.aigiscode/`
--external-tool <name>       Run external analyzers (`ruff`, `gitleaks`, `pip-audit`, `osv-scanner`, `phpstan`, `composer-audit`, `npm-audit`, `cargo-deny`, `cargo-clippy`, `all`)
-P <plugin>                   Select a built-in plugin profile
--plugin-module <path.py>     Load an external Python plugin module
--policy-file <path.json>     Override policy from a JSON file
-v / --verbose                Enable debug logging
```

## For AI Agents

AigisCode is designed to be consumed by AI coding agents as evaluation
infrastructure. The primary machine interface is the JSON report:

```
.aigiscode/aigiscode-report.json
```

It contains structured data for every finding category, metric, and contract
inventory --- ready for downstream planning, triage, and automated remediation
without parsing prose.

Important lifecycle field:
- `feedback_loop`
  - `detected_total`
  - `accepted_by_policy`
  - `actionable_visible`
  - `informational_visible`
  - `external_visible`
  - `ai_reviewed`
  - `rules_generated`
  - `next_run_should_improve`

Important resume field:
- `agent_handoff`
  - `summary`
  - `priorities`
  - `accepted_noise`
  - `needs_context`
  - `next_steps`
  - `verification_commands`
  - `coverage_warnings`

Important imported-security fields:
- `external_analysis.tool_runs`
  - execution status, artifact paths, and per-tool summaries
- `external_analysis.findings`
  - normalized findings with tool/rule provenance and stable fingerprints
- `security_review`
  - AI triage results for imported security findings during `analyze`
- `review.verdicts`
  - AI verdicts for native dead-code and hardwiring findings

### Recommended Agent Workflow

1. Run `aigiscode analyze /repo` --- generate baseline report
2. Parse `.aigiscode/aigiscode-report.json` --- read structured findings
3. Sample findings and classify (true positive / false positive / uncertain)
4. Encode narrow policy for repeated false positives in `.aigiscode/policy.json`
5. Run `aigiscode report /repo` --- fast re-evaluation after policy changes
6. Run `aigiscode tune /repo -i 2` --- optional AI-guided policy refinement

### Key JSON Fields for Agents

| JSON Path | Description |
|---|---|
| `graph_analysis.strong_circular_dependencies` | Architectural cycle triage |
| `graph_analysis.circular_dependencies` | Broader runtime context |
| `dead_code` | Unused imports, methods, properties, classes |
| `hardwiring` | Magic strings, repeated literals, hardcoded network |
| `security` | Security-focused summary of hardcoded network/env findings |
| `external_analysis` | Imported findings and archived raw artifacts from external analyzers |
| `security_review` | AI verdicts for imported external security findings reviewed during `analyze` |
| `feedback_loop` | Self-healing lifecycle summary across rules, informational policy, external findings, and AI review |
| `extensions.contract_inventory` | Routes, hooks, env keys, config keys |

See [docs/AI_AGENT_USAGE.md](docs/AI_AGENT_USAGE.md) for the full agent
integration guide.

## Configuration

AigisCode is policy-driven. Instead of hard-coding project-specific behavior
into the analyzer, express it through a JSON policy file:

```json
{
  "graph": {
    "js_import_aliases": { "@/": "src/" },
    "orphan_entry_patterns": ["src/bootstrap/**/*.ts"],
    "layer_violation_excludes": ["resources/js/**"]
  },
  "dead_code": {
    "abandoned_entry_patterns": ["/Contracts/"],
    "abandoned_languages": ["php"]
  },
  "hardwiring": {
    "repeated_literal_min_occurrences": 4,
    "skip_path_patterns": ["app/Console/*"],
    "informational_path_patterns": ["website/src/pages/**"],
    "informational_value_regexes": ["^https://aigiscode\\.com"],
    "informational_context_regexes": ["canonical|og:url|schema"],
    "js_env_allow_names": ["DEV", "PROD", "MODE"]
  },
  "ai": {
    "allow_claude_fallback": true
  }
}
```

Use informational hardwiring policy when a value is legitimate repository context that should remain visible but should not count as actionable debt. Use saved rules when the finding is a durable false-positive pattern that should disappear on the next run.

External analyzers follow the same product direction: raw artifacts are archived, normalized findings land in `external_analysis`, and imported findings should converge into the same rules / AI-review / feedback-loop lifecycle instead of remaining a permanent side channel.

Policy is merged in layers: built-in defaults, selected plugins, auto-detected
plugins, external plugin modules, project file (`.aigiscode/policy.json`),
and ad-hoc `--policy-file`. Later layers override earlier ones.

### Built-in Plugins

| Plugin | Description |
|---|---|
| `generic` | Safe defaults for mixed-language repositories (always loaded) |
| `django` | Django-aware runtime conventions and entry points |
| `wordpress` | WordPress admin and hook conventions |
| `laravel` | Laravel-specific entry points and dynamic contexts |

### External Plugins

Write a Python module with `build_policy_patch()` and optional runtime hooks:

```python
def build_policy_patch(project_path, selected_plugins):
    return {
        "dead_code": {
            "abandoned_entry_patterns": ["/app/Legacy/"]
        }
    }

# Optional: refine results at runtime
def refine_graph_result(graph_result, graph, store, project_path, policy):
    return graph_result

def refine_dead_code_result(dead_code_result, store, project_path, policy):
    return dead_code_result
```

```bash
aigiscode analyze /repo --plugin-module ./my_plugin.py
```

See [docs/PLUGIN_SYSTEM.md](docs/PLUGIN_SYSTEM.md) for the full plugin
documentation.

## Architecture

The system separates generic analysis from project-specific interpretation
across four layers of responsibility:

1. **Index and graph construction** --- generic, language-aware parsing
2. **Generic detectors** --- emit candidates, not verdicts
3. **Policy and exclusion rules** --- project-specific adaptation
4. **AI review and tuning** --- final-stage classification

Design principles: decoupling over convenience, explainable heuristics over
opaque model-only decisions, partial but explicit coverage over false certainty.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design document.

## Requirements

- Python 3.12+
- Optional: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` for AI-assisted review
- Optional: authenticated `codex` CLI session (`~/.codex/auth.json`) for ChatGPT-authenticated Codex usage without relying on the Python API path

Core dependencies: tree-sitter, NetworkX, Pydantic, Typer, Rich.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for
guidelines on development setup, testing, and pull request conventions.

Before patching the analyzer core, consider whether the issue can be expressed
through policy or a plugin module. The design boundary is intentional:
generic analysis logic belongs in the core, project-specific behavior belongs
in policy.

## License

[MIT](LICENSE)

---

<p align="center">
  <sub>AigisCode --- your codebase's shield against architectural decay.</sub>
</p>
