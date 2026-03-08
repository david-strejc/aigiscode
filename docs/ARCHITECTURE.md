# Architecture

## Purpose

`codexaudit` evaluates codebases in a way that is:
- machine-readable
- policy-driven
- adaptable across projects
- usable by AI agents without rewriting core logic for each repository

The core design goal is to separate generic analysis from project-specific interpretation.

## Core Model

The system has four layers of responsibility:

1. index and graph construction
2. generic detectors
3. project policy and exclusion rules
4. AI-assisted review and tuning

Only the first two layers should contain generic analysis logic. Project-specific behavior belongs in policy or plugin modules.

## Pipeline

### 1. Index

The indexer parses supported files and stores:
- files
- symbols
- dependencies
- semantic envelopes

Current parsing backends:
- `tree-sitter` for PHP, TypeScript, JavaScript, and Vue
- Python `ast` for Python source

Storage is SQLite in `.codexaudit/codexaudit.db`.

Incremental indexing prunes stale file rows automatically. When parser behavior changes, `analyze --reset` is the explicit full-refresh path.

### 2. Graph Analysis

Graph analysis builds a file-level dependency graph and computes:
- strong circular dependencies
- total circular dependencies
- coupling
- bottlenecks
- layer violations
- likely orphan files
- runtime entry candidates
- god classes

This stage is meant to answer architectural questions, not stylistic ones.

The graph now distinguishes two related but different concepts:
- runtime truth: include/load/bootstrap edges are useful for understanding how code is reached
- architectural truth: strong-cycle reporting excludes load-only edges so framework bootstraps do not dominate the cycle metric
- registration truth: callback/class-string registration edges keep listener/controller/handler wiring explicit enough for graph and dead-code analysis

### 3. Detector Passes

Detectors currently include:
- dead code
- hardwiring

Detectors are intentionally generic. They should emit candidates, not project-specific verdicts.

Detector coverage can still be partial by language. That state must be surfaced in reports instead of silently treating newly indexed languages as fully covered.

Current hardwiring design is precision-first:
- strip syntax-only noise before counting findings
- analyze `.vue` files from script sections instead of template markup
- include Python in generic string/network/env analysis
- require sink/context evidence for entity coupling
- keep repeated-literal reporting focused on route, selector, path, and API contracts
- gate simple magic-string tokens by signal context and suppress metadata-heavy contexts
- suppress protocol/header/query/page markers when the surrounding context proves standards-driven or framework-contract usage
- distinguish operational URLs from documentation/help/schema/namespace URLs before emitting `hardcoded_network`
- harvest explicit symbolic contracts from type unions, `as const` arrays, and registration maps before treating later string comparisons as generic hardwiring

### 4. Rule Filtering

Saved rules in `.codexaudit/rules.json` pre-filter known false positives.

Rules are the durable memory of prior audits. They should stay narrow and explainable.

### Built-in Extensions

The report layer now also emits built-in derived context under `report.extensions`.

Current built-in extension:
- `contract_inventory`
  - routes
  - hooks
  - registered keys
  - symbolic literals
  - env keys
  - config keys

This is descriptive evidence, not a detector. The point is to make runtime contracts explicit so later detector and policy work has better grounding.

That grounding is now used by hardwiring analysis: declared contract values are excluded from generic repeated-literal duplication findings.
It is also used to suppress magic-string findings when a literal is already a declared runtime contract.

### 5. AI Review

AI review examines remaining findings and can:
- classify true positives versus false positives
- propose new exclusion rules
- synthesize higher-level insights

AI review is intentionally downstream of static analysis. The deterministic engine generates candidates; Codex is the final reviewer that decides whether a sampled finding is acceptable, wrong, or needs more context.
Codex SDK is the primary backend for that stage; Codex CLI and Claude are optional fallbacks.

### 6. Analytical Mode and Tune

Analytical mode asks Codex for a policy patch.

Tune mode is stricter:
- it proposes a candidate patch
- reruns metrics
- accepts only non-regressive improvements

This prevents blind optimization against a single weighted score.

## Policy Boundary

Policy has four sections:
- `graph`
- `dead_code`
- `hardwiring`
- `ai`

Examples of policy responsibilities:
- JS alias resolution
- layer name overrides
- orphan entrypoint patterns
- runtime entrypoint patterns
- framework entry-point patterns
- abandoned-class language scope
- dynamic reference surfaces for dead-code rescue
- hardwiring entity require-context regexes
- hardwiring allow-context regexes
- magic-string signal/noise context regexes
- repeated-literal skip regexes
- literal thresholds and path excludes
- allowed JS env names
- AI backend ordering

Examples of non-policy responsibilities:
- parsing source code
- building the dependency graph
- generic detector algorithms

## Extension Points

There are two extension mechanisms today:

1. plugin profiles
   Built-in named profiles such as `generic`, `laravel`, `newerp`, `django`, and `wordpress`.
2. external plugin modules
   Python modules that can return policy patches dynamically and optionally refine graph/dead-code/hardwiring/report results at runtime.
   They can now also enrich declared contract lookup and refine hardwiring findings per category before the final result is aggregated.

This keeps the system decoupled without turning every project into a fork of the analyzer.

## Design Principles

- Decoupling over convenience.
- DRY in backend and policy orchestration.
- YAGNI for detector complexity until a real corpus proves the need.
- Prefer explainable heuristics over opaque model-only decisions.
- Treat counts as signals, not goals.
- Prefer partial but explicit coverage over false certainty.

## Non-Goals

The system is not trying to be:
- a fully sound program analysis engine
- a universal architecture truth machine
- an auto-suppressor that hides findings until the dashboard looks good

It is a practical evaluator for large real repositories.
