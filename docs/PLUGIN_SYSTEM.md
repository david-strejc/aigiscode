# Plugin System

## Purpose

The plugin system exists to keep `codexaudit` core logic generic.

Different codebases have different:
- entry points
- architectural names
- dynamic runtime conventions
- valid uses of strings, env access, and framework hooks

Those differences should be expressed through policy, not hardcoded into detector logic.

## Design Boundary

Plugins and policy should handle:
- project conventions
- path patterns
- aliases
- allowed dynamic contexts
- dynamic entrypoint and manifest surfaces
- entity sink and allow-context regexes
- magic-string signal/noise context regexes
- repeated-literal skip regexes
- detector thresholds
- orphan entrypoints
- allowed JS env names
- AI backend preferences

Plugins should not handle:
- parsing code
- building the graph
- generic detector algorithms

That boundary is what keeps the system reusable across repositories.

## Policy Shape

Policy has four sections:
- `graph`
- `dead_code`
- `hardwiring`
- `ai`

Example patch:

```json
{
  "graph": {
    "js_fuzzy_import_resolution": false,
    "js_import_aliases": {
      "@/": "resources/js/"
    },
    "layer_patterns": {
      "contracts": "Repository"
    },
    "layer_violation_excludes": ["resources/js/**"],
    "orphan_entry_patterns": ["app/Actions/**/*.php"]
  },
  "dead_code": {
    "attribute_usage_names": ["Override", "EntityAttr"],
    "abandoned_languages": ["php"],
    "abandoned_entry_patterns": ["/Contracts/"],
    "abandoned_dynamic_reference_patterns": ["**/bootstrap/**/*.php"]
  },
  "hardwiring": {
    "entity_context_require_regexes": [
      "\\b(?:input|query)\\(\\s*['\\\"]entityTypes?['\\\"]\\s*,"
    ],
    "entity_context_allow_regexes": [
      "['\\\"]entity(Type)?['\\\"]\\s*=>",
      "\\blookup(Id)?\\s*\\("
    ],
    "magic_string_signal_context_regexes": [
      "\\$(?:mode|status|scope|provider|backend|algorithm|phase|event)\\b"
    ],
    "magic_string_noise_context_regexes": [
      "\\[(?:'|\\\")(?:options|relation|type|storage|panel|key)(?:'|\\\")\\]"
    ],
    "repeated_literal_skip_regexes": [
      "^\\s*--[a-z0-9-]+(?:=.*)?\\s*$"
    ],
    "repeated_literal_min_occurrences": 4,
    "repeated_literal_min_length": 5,
    "skip_path_patterns": ["app/Console/*"],
    "js_env_allow_names": ["DEV", "PROD", "MODE"]
  },
  "ai": {
    "allow_claude_fallback": true
  }
}
```

## Merge Order

Policy is merged in this order:

1. `generic` plugin
2. explicitly selected plugins from `-P`
3. auto-detected plugins such as `laravel` and `newerp`
4. external plugin modules from `--plugin-module`
5. project file `.codexaudit/policy.json`
6. ad-hoc `--policy-file`

Later layers override earlier layers. Lists are deduplicated during normalization.

## Built-in Plugins

- `generic`
  Safe defaults for mixed-language repositories.
- `laravel`
  Laravel-aware entry points and dynamic contexts.
- `newerp`
  NewERP-specific conventions for `app/` and `resources/js/`.
- `django`
  Django-aware runtime plugin profile for framework conventions.
- `wordpress`
  WordPress-aware runtime plugin profile for admin/runtime conventions.

## External Plugin Modules

External Python modules can generate policy dynamically:

```bash
codexaudit analyze /repo --plugin-module ./my_plugin.py
```

Contract:

```python
def build_policy_patch(project_path, selected_plugins):
    return {
        "dead_code": {
            "abandoned_entry_patterns": ["/app/Legacy/"],
            "abandoned_dynamic_reference_patterns": ["**/bootstrap/**/*.php"]
        }
    }
```

Optional runtime hooks:

```python
def refine_contract_lookup(contract_lookup, store, project_path, policy):
    return {"symbolic_literals": ["reply-all"]}

def refine_hardwiring_findings(findings, category, store, project_path, policy, contract_lookup):
    return findings

def refine_graph_result(graph_result, graph, store, project_path, policy):
    return graph_result

def refine_dead_code_result(dead_code_result, store, project_path, policy):
    return dead_code_result

def refine_hardwiring_result(hardwiring_result, store, project_path, policy):
    return hardwiring_result

def build_report_extensions(report, graph, store, project_path, policy):
    return {"custom_metric": 1}
```

`refine_contract_lookup` may return a `ContractLookup` or a `dict` patch with keys like `routes`, `hooks`, `registered_keys`, `symbolic_literals`, `env_keys`, and `config_keys`.
`refine_hardwiring_findings` runs before the final hardwiring result is assembled, so framework plugins can demote or remove category-specific noise without forking detector core.
`build_policy_patch` must return a `dict`. Runtime hooks may return the refined result object or `None` to leave it unchanged.
`build_report_extensions` payloads are emitted under `codexaudit-report.json.extensions` and in the Markdown report.

Use a plugin module when:
- a rule depends on repository layout
- the rule should be computed, not copied into static JSON
- multiple repos will share the same policy logic
- the repo needs result refinement that policy alone cannot express cleanly

## Recommended Workflow

1. Start from `generic`.
2. Add only the plugin profiles that clearly fit the codebase.
3. Run a baseline report.
4. Sample real false positives.
5. Add a minimal policy patch.
6. Move repeated project logic into a plugin module if needed.

## AI Agent Guidance

An AI agent should prefer changing policy over changing analyzer code when:
- the false positive is project-specific
- the distinction can be expressed through an existing policy field
- the same detector is otherwise useful on other repositories

An AI agent should patch `codexaudit` core only when policy cannot represent the distinction cleanly.

See:
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- [docs/AI_AGENT_USAGE.md](AI_AGENT_USAGE.md)
- [docs/RELIABILITY.md](RELIABILITY.md)
