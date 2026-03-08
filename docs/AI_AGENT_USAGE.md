# AI Agent Usage

## Purpose

This document describes how another AI agent should use `codexaudit` against a repository.

The short version:
- use it as a local evaluation engine
- consume JSON output
- tune policy in small steps
- verify samples before trusting counts

## Setup

Install:

```bash
uv pip install -e .
```

Optional backends:

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

## Recommended Agent Loop

### Baseline

```bash
codexaudit analyze /repo
```

If the repo was already indexed and only policy changed:

```bash
codexaudit report /repo
```

### Read Machine Output

Primary machine interface:

```text
/repo/.codexaudit/codexaudit-report.json
```

An agent should prefer the JSON report over Markdown for:
- category counts
- per-finding sampling
- downstream planning
- plugin-provided derived metrics under `extensions`

Important graph fields:
- `strong_circular_dependencies` for architectural cycle triage
- `circular_dependencies` for broader runtime/load context
- `orphan_files` for higher-signal likely dead files
- `runtime_entry_candidates` for loader/front-controller review
- `register` edges in the underlying graph when callback/class-string registration is part of runtime reachability

Important built-in extension:
- `extensions.contract_inventory`
  - `routes`
  - `hooks`
  - `registered_keys`
  - `symbolic_literals`
  - `env_keys`
  - `config_keys`

### Sample Findings

Do not modify policy from totals alone.

Sample:
- structural findings
- dead-code findings
- hardwiring findings

Then classify:
- true positive
- false positive
- uncertain

The intended role of the agent is final-stage review, not first-pass detection. Static analysis produces candidate findings; Codex should confirm, reject, or downgrade them based on code context and contract evidence.

Current validation lesson:
- do not trust a raw hardwiring queue just because the total count went down
- prefer categories that reviewers already consider stronger (`hardcoded_ip_url`, `hardcoded_entity`, and selected `env_outside_config`)
- treat `repeated_literal` as exploratory unless sampled precision is proven on that repo

### Encode Narrow Policy

If false positives repeat, add:
- `.codexaudit/policy.json`
- `--policy-file`
- `--plugin-module`

Prefer the smallest change that explains the false-positive pattern.

Common examples:
- `graph.orphan_entry_patterns`
- `graph.layer_violation_excludes`
- `dead_code.abandoned_entry_patterns`
- `dead_code.abandoned_dynamic_reference_patterns`
- `dead_code.abandoned_languages`
- `hardwiring.entity_context_require_regexes`
- `hardwiring.entity_context_allow_regexes`
- `hardwiring.repeated_literal_skip_regexes`
- `hardwiring.magic_string_skip_path_patterns`
- `hardwiring.magic_string_signal_context_regexes`
- `hardwiring.magic_string_noise_context_regexes`
- `hardwiring.js_env_allow_names`

### Re-run

Re-run `report` or `analyze` and compare:
- counts
- strong-vs-total cycle spread
- confidence distribution (`high|medium|low`) for hardwiring
- sampled precision
- whether any structural metric regressed
- whether `unsupported_source_files` makes the run partial rather than full-coverage
- whether `summary.detector_coverage` shows detector-level partial coverage even when indexing coverage is full
- whether a clean `--reset` run is needed because parser/indexer behavior changed since the last index

Triage order:
1. review `high` confidence findings first
2. then sample `medium` confidence findings
3. treat `low` confidence findings as exploratory unless repeated business impact is proven
4. if reviewer sampling shows the `high` bucket is still noisy, do not let AI auto-adjudicate the raw queue; narrow categories first

### Optional Tune

Once policy is reasonable:

```bash
codexaudit tune /repo -i 2
```

Treat tune output as a candidate, not automatic truth.

## Adapting To Other Codebases

Start minimal:

```bash
codexaudit analyze /repo -P generic
```

Then layer project knowledge:

1. add built-in plugins if they fit
2. add a small `policy.json`
3. add a Python plugin module if behavior depends on repo structure

Example plugin module:

```python
def build_policy_patch(project_path, selected_plugins):
    return {
        "graph": {
            "js_import_aliases": {
                "@/": "src/"
            },
            "orphan_entry_patterns": ["src/bootstrap/**/*.ts"]
        },
        "dead_code": {
            "abandoned_entry_patterns": ["/src/bootstrap/"],
            "abandoned_dynamic_reference_patterns": ["src/bootstrap/**/*.ts"],
            "abandoned_languages": ["php"]
        },
        "hardwiring": {
            "entity_context_require_regexes": [
                "\\b(?:input|query)\\(\\s*['\\\"]entityTypes?['\\\"]\\s*,"
            ],
            "repeated_literal_skip_regexes": [
                "^\\s*--[a-z0-9-]+(?:=.*)?\\s*$"
            ],
            "magic_string_signal_context_regexes": [
                "\\$(?:mode|status|scope|provider|backend|algorithm|phase|event)\\b"
            ],
            "magic_string_noise_context_regexes": [
                "\\[(?:'|\\\")(?:options|relation|type|storage|panel|key)(?:'|\\\")\\]"
            ],
            "js_env_allow_names": ["DEV", "PROD", "MODE"]
        }
    }
```

If policy is not enough, the same plugin module can also expose:
- `refine_contract_lookup(...)`
- `refine_hardwiring_findings(...)`
- `refine_graph_result(...)`
- `refine_dead_code_result(...)`
- `refine_hardwiring_result(...)`
- `build_report_extensions(...)`

## What Agents Should Not Do

- Do not accept lower counts without sampling the new findings.
- Do not widen suppressions until the pattern is clear.
- Do not treat AI review as a substitute for reading the referenced code.
- Do not patch `codexaudit` core when policy can express the rule.

## Good Defaults

- use `report` for fast re-evaluation after policy changes
- use `analyze` when index or AI review must be refreshed
- store project-local policy in `.codexaudit/policy.json`
- keep plugin modules repository-specific
- treat `summary.detector_coverage` as a hard warning before trusting detector totals on a newly supported language
- treat `runtime_entry_candidates` as a policy/plugin opportunity, not immediate dead code
- inspect `extensions.contract_inventory` before widening hardwiring suppressions; many “magic strings” are really declared contracts
- declared contracts are now already used to suppress generic repeated-literal noise, so inspect what remains before adding more skip rules
- built-in inventory is now runtime-focused and skips test/fixture files, so it is safer to use as evidence for production code analysis

## Escalation Rule

Patch the analyzer itself only when:
- the finding is wrong across multiple codebases
- the bug is in generic parsing or detector logic
- policy cannot represent the distinction cleanly
