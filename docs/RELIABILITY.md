# Reliability

## What Reliable Means Here

In `codexaudit`, reliability means:
- findings are reproducible from the indexed codebase
- project-specific behavior is configurable outside core code
- policy tuning cannot silently worsen architectural signal
- AI output is constrained by static results and explicit guardrails

It does not mean every finding is a true positive.

## Reliability Layers

### Static Core

The static core provides deterministic results from the indexed project:
- graph structure
- dead-code candidates
- hardwiring candidates

This is the foundation. If the static core is wrong, policy should not be used to hide the mistake.

### Policy

Policy improves precision by describing legitimate project behavior:
- aliases
- entry points
- framework attributes
- allowed dynamic contexts
- path exclusions
- detector thresholds

Policy is where codebase-specific truth belongs.

### Rules

Rules persist audited false positives across runs.

Use rules when:
- a finding is locally invalid
- the invalidity is stable
- the suppression can be described narrowly

Do not use rules to erase whole categories of noise.

### AI Review

AI review should be used to:
- inspect remaining candidates
- generate narrow exclusions
- summarize risk areas

AI review should not be trusted without spot-checking a sample of findings.

## Detector Reliability By Category

Typical ordering:

1. structural findings
   Usually the highest-signal category
2. dead code
   Moderate reliability, sensitive to dynamic framework hooks and reflection
3. hardwiring
   Broadest category, useful for discovery but most likely to need tuning

## Tune Guardrails

`tune` is intentionally conservative.

A patch is accepted only when:
- at least one tracked metric improves
- no tracked metric regresses
- the candidate still validates as policy

This is a Pareto-style gate, not blind score chasing.

## Recommended Validation Method

When evaluating a new codebase:

1. run a baseline report
2. sample real findings from each major category
3. classify them manually or with independent AI review
4. encode only repeated false-positive patterns into policy
5. rerun and compare both counts and sampled precision

This matters more than raw totals.

## Current Known Limits

- Dynamic framework entry points can still create dead-code and orphan noise when load/bootstrap surfaces are not yet modeled.
- Load/bootstrap edges can inflate total cycle counts, so strong-cycle reporting should be preferred for architecture scoring.
- Runtime callback/class-string registration is now indexed explicitly via `register` edges, which improves abandoned-class and orphan truth without relying only on string rescue heuristics.
- Reports now ship a built-in contract inventory, which makes route/hook/env/config landscapes explicit before policy or detector changes.
- Reports now ship a built-in contract inventory, which makes route/hook/env/config landscapes explicit before policy or detector changes.
- Contract inventory now also harvests explicit symbolic literals from type unions, `as const` arrays, and registration maps so declared string protocols can be distinguished from ad hoc string branching.
- Declared contract values now suppress generic repeated-literal findings, which reduces false duplication noise in framework-heavy repos.
- Declared contracts and protocol literals now suppress part of the remaining magic-string bucket as well.
- Magic strings now also suppress SQL/operator/header/charset/page-marker noise when the surrounding context proves the string is framework or standards-driven.
- Hardcoded-network now filters non-runtime documentation/help/schema/namespace URLs instead of treating them like operational endpoints.
- Hardwiring results are now much tighter, but `magic_string` still needs context tuning.
- JS and Vue findings benefit from project-specific conventions, especially for UI state tags and filter/operator names.
- Structural precision improves significantly when orphan entrypoints and config-registration paths are represented in policy.
- Saved rules now prefilter `report` and `analyze --skip-review`, so fast local runs no longer lose audited exclusions.
- Unsupported-language coverage is now surfaced explicitly so unsupported repos do not appear falsely healthy.
- Detector partial coverage is surfaced separately, so newly indexed languages do not falsely imply full detector support.
- Incremental indexing prunes stale file rows automatically, but parser/indexer changes should still be validated with a clean `--reset` run.
- There is not yet a golden-set regression harness in CI.

## March 7, 2026 Retest

The system was retested against `../newerp` in report mode after reindexing and applying saved rules.

Observed report counts:

- `cycles`: `25`
- `god_classes`: `300`
- `layer_violations`: `14`
- `orphans`: `560`
- `runtime_entry_candidates`: `1006`
- `detector_coverage`: `dead_code: javascript, typescript, vue`
- `dead_code`: `69`
- `unused_imports`: `55`
- `unused_methods`: `10`
- `unused_properties`: `4`
- `abandoned_classes`: `0`
- `hardwiring`: `1377`
- `hardcoded_entity`: `235`
- `magic_string`: `90`
- `repeated_literal`: `817`
- `hardcoded_ip_url`: `180`
- `env_outside_config`: `55`
- `saved-rule prefilter`: `43`

Observed precision notes:

- dead code improved further after:
  - rejecting stale symbol/dependency rows when the current source line no longer matches the indexed declaration
  - adding Python unused-import detection with low-confidence treatment for `__init__.py` re-export surfaces
  - recognizing PHPDoc-only import usage
  - recognizing callback-style private-method references
  - rescuing classes referenced only by runtime string class names
  - restricting abandoned-class analysis to configured languages (`php` by default)
  - rescuing classes referenced through hooks, manifests, bootstrap files, routes, and service providers
- orphan precision improved after honoring policy-defined orphan entrypoint patterns such as `*.hooks.php` and `*.actions.php`
- runtime-entry precision improved further after classifying load-driven and configured entry files into `runtime_entry_candidates` instead of mixing them into `orphan_files`
- abandoned-class precision improved further after treating callback/class-string registration as first-class `register` dependencies
- god-class precision improved materially after:
  - excluding test files from god-class scoring
  - stopping file-level dependency totals from flagging every small class in multi-class files
  - requiring dependency-only god classes to be long single-class files
- hardwiring improved materially after:
  - stripping `.vue` template markup from hardwiring analysis
  - requiring entity sink/context evidence before reporting `hardcoded_entity`
  - filtering repeated literals with policy-driven skip regexes
  - gating simple magic-string tokens by signal context and suppressing metadata-heavy contexts
  - suppressing standards-driven protocol/query/header/page markers in context
  - suppressing later uses of explicitly declared symbolic literals from type unions, `as const` arrays, and PHP registration maps
  - skipping tests/fixtures and tooling env reads more consistently
  - ignoring selector literals and build-task labels in hardwiring mode
  - tightening IP detection so SVG/path decimals do not become fake network findings
  - filtering documentation/help/schema/namespace URLs out of `hardcoded_network`
  - lowering confidence for provider-registry and resource-hint URLs instead of treating them like hard operational endpoints
  - suppressing settings/header/module/action/page contract strings more aggressively when surrounding context proves they are framework or UI control tokens
- hardwiring now includes Python for generic string/network/env detection
- remaining hardwiring noise is still concentrated in `repeated_literal`, with `magic_string` much smaller but still not trustworthy enough for final-stage triage on `newerp`

Sample verdicts from the retest:

- dead code sample: `2/3` correct before the TS property fix; the main miss was an obvious live TS property
- structural sample: real model/service coupling exists, but lifecycle hook files needed explicit entrypoint handling
- repeated-literal sample before the rewrite was about `23%` strict precision and was dominated by template and import-path artifacts
- hardcoded-entity sample after the rewrite was about `45%` strict precision and about `59%` including arguable cases
- `magic_string` count dropped from `579` to `227` in this pass; it remains the weakest category and should still be sampled before trust
- low-confidence findings are currently concentrated in `repeated_literal`, which should be treated as exploratory feed rather than strict gate
- latest agent verdict on `newerp`: `not acceptable` for final-stage AI triage without stricter filtering of `repeated_literal`, provider/default URLs, protocol tokens, and entity-local cases

## Cross-Framework Validation

Retest on `/home/david/Work/Programming/django` on March 7, 2026:

- `files_indexed`: `2929`
- `symbols_extracted`: `46064`
- `dependencies_found`: `25311`
- `cycles`: `100`
- `god_classes`: `102` after the heuristic fix, down from `5734`
- `layer_violations`: `185`
- `orphans`: `71`
- `runtime_entry_candidates`: `1167`
- `dead_code`: `151` (`unused_imports` only)
- `hardwiring`: `155`
- `magic_string`: `9`
- `env_outside_config`: `9`
- `detector_coverage`: `dead_code: javascript`

What this means:

- Python graph indexing is now real and useful on a large framework codebase.
- Dead-code support for Python is still partial and currently centers on imports.
- Hardwiring support is much cleaner after tooling/test-path cleanup, contract suppression, and non-runtime URL suppression, but env conventions and standards/protocol tokens still need more discrimination.
- Built-in contract inventory can now expose runtime surfaces even when a detector category is still partial.
- latest agent verdict on Django: `conditionally acceptable` for final-stage AI triage if `hardcoded_ip_url` is prioritized and `env_outside_config` plus most `magic_string` findings are down-ranked

Retest on `/home/david/Work/Programming/wordpress` on March 7, 2026:

- `files_indexed`: `3340`
- `symbols_extracted`: `33804`
- `dependencies_found`: `6791`
- `cycles`: `9` strong, `64` total
- `god_classes`: `150`
- `layer_violations`: `0`
- `orphans`: `156`
- `runtime_entry_candidates`: `1201`
- `dead_code`: `120`
- `hardwiring`: `2189`
- `magic_string`: `450`
- `repeated_literal`: `1455`
- `hardcoded_ip_url`: `284`
- `detector_coverage`: `dead_code: javascript, typescript`

Agent-audited lessons from WordPress:

- structural:
  - many orphan files were runtime-entry false positives created by `require/include`, autoload maps, and factory-by-name loaders
  - separating `runtime_entry_candidates` and `strong_circular_dependencies` made the graph report substantially more honest
  - zero layer violations is low-signal without an explicit layer model
- hardwiring:
  - many JS strings are selectors, admin action slugs, or build task labels rather than true hardwiring
  - docs/spec/help URLs and tooling env reads should not be treated like runtime network/config debt
  - declared hooks/options/routes should not be treated as generic repeated-literal duplication, and the built-in contract inventory now removes part of that noise
  - declared registration keys and protocol literals should not be treated as generic magic strings, and that now removes another substantial slice of WordPress noise
  - context-aware page/header/query suppression plus non-runtime URL filtering cut the next WordPress bucket again, but action slugs, screen IDs, parser constants, and provider/CDN URLs still remain
  - explicit symbolic-contract harvesting removed another slice of `newerp` and WordPress magic-string noise without hiding real entity/env/network residue
- dead code:
  - PHPDoc imports and callback methods were real generic misses and are now fixed
  - remaining abandoned classes are still heavily influenced by runtime loader patterns
- latest agent verdict on WordPress: `not acceptable` for final-stage AI triage because the `magic_string high` bucket still contains too many admin action names, screen IDs, parser constants, and other framework-local contracts

What this changes in the reliability model:

- the system is now validated on three substantially different repositories: `newerp`, Django, and WordPress core
- architecture scoring now prefers strong cycles over total cycles, while still preserving load-driven cycle context in the JSON report
- the preferred improvement loop is now explicit:
  1. make a generic detector/indexer change
  2. rerun on all validation repos
  3. send independent agents to sample findings
  4. keep or revert the change based on cross-repo evidence

## Next Reliability Work

- build a golden corpus of true and false findings
- measure precision by category
- add CI gates for detector regressions
- extend the runtime plugin API into fully custom detector categories when a real multi-repo need appears
- tighten Python dead-code coverage beyond imports
- tighten hardcoded-network precision further on provider/CDN versus true environment-coupling URLs
