# Analytical Mode

## Purpose

Analytical mode asks Codex to propose a policy patch from the current codebase metrics and active policy.

It is for:
- discovering likely precision improvements
- generating a first draft of project policy
- helping an AI agent adapt the evaluator to a new repository

It is not an automatic approval mechanism.

## Command

```bash
codexaudit analyze /path/to/project --analytical-mode
```

## What It Does

After analysis and report generation:

1. builds a compact summary of structural and detector metrics
2. serializes the active merged policy
3. sends both to Codex with a strict JSON schema
4. writes the returned patch to `.codexaudit/policy.suggested.json`

Patch suggestions can include:
- graph alias and layer settings
- orphan entrypoint patterns
- dead-code entry-point patterns
- dead-code dynamic reference surfaces and language scope
- hardwiring entity require-context regexes
- hardwiring allow-context regexes
- magic-string signal/noise context regexes
- repeated-literal skip regexes
- hardwiring thresholds and skip paths
- allowed JS env names
- AI backend fallback preferences

## Guardrails

- JSON-only output is requested
- the current policy is not mutated automatically
- if no backend is available, no patch is generated
- the patch still goes through normal policy validation on the next run

## How To Use It

Recommended sequence:

1. run baseline

```bash
codexaudit analyze ../newerp -P newerp
```

2. generate a candidate patch

```bash
codexaudit analyze ../newerp -P newerp --analytical-mode
```

3. inspect the generated file

```text
../newerp/.codexaudit/policy.suggested.json
```

4. apply it with `--policy-file` or move it into `.codexaudit/policy.json`

5. rerun `report` or `analyze` and sample real findings before accepting it as a new baseline

## Relationship To Tune

Analytical mode only proposes a patch.

`tune` is stricter:
- it applies a candidate patch
- reruns metrics
- accepts only changes with measurable improvement and no metric regressions

Use analytical mode when you want ideas.
Use tune when you want guarded iteration.

## AI Agent Guidance

An AI agent should use analytical mode as a generator of candidate policy, not as final truth.

A good agent loop is:

1. generate patch
2. inspect changed policy fields
3. run `report`
4. sample findings manually or with independent AI review
5. keep only narrow, justified changes

See:
- [docs/AI_AGENT_USAGE.md](AI_AGENT_USAGE.md)
- [docs/RELIABILITY.md](RELIABILITY.md)
