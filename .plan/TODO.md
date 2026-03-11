# TODO

## Backlog
- [ ] Extend Rust-specific external analysis using a large reference workspace: evaluate `cargo-audit` vs `osv-scanner` overlap for Rust SCA, decide whether `cargo-geiger` adds enough value as an optional unsafe-surface signal, and tune defaults around the shipped `cargo-deny`/`cargo-clippy` integrations.
- [ ] Calibrate Rust detector fidelity against `zed`: after enabling native Rust dead-code/hardwiring support, the real report now shows `3822` dead-code findings and `901` hardwiring findings on the current clone (`1725` files, `1706` Rust files, `60695` symbols, `41722` dependencies). Reduce obvious noise before trusting those counts as actionability signals.
- [ ] Fix `analyze --skip-synthesis` so it actually skips Phase 3 semantic-envelope generation; the flag was ignored during real `../zed` runs on March 11, 2026.
- [ ] Make semantic-envelope generation scale for large repos like `../zed`; the live run only advanced a few files after minutes, which blocks end-to-end AI synthesis on real Rust-heavy codebases.
- [ ] Add end-to-end coverage for AI review/report serialization using a reproducible local backend harness instead of stubs.
- [ ] Evaluate optional integration points for Semgrep, CodeQL, gitleaks/trufflehog, and dependency vulnerability scanners.
- [ ] Rename remaining internal/backend identifiers that still say `codex_sdk` even though the Python path uses the OpenAI Responses API directly.
- [ ] Add backend provenance to AI-generated report sections so downstream agents can distinguish Codex SDK, Codex CLI, and Claude-backed outputs.
- [ ] Design a normalized security finding schema with stable fingerprints, tool provenance, SARIF import/export, and package/license metadata for external scanners.
- [ ] Prototype external scanner ingestion for `osv-scanner`, `gitleaks`, `bandit`, and `psalm`, preserving raw artifacts under `.aigiscode/reports/<run-id>/raw/`.
- [ ] Decide whether Semgrep is acceptable despite LGPL engine licensing and the Semgrep Rules License on the maintained rules repo, or whether custom/internal rules should be the default.
- [ ] Decide whether CodeQL can be offered only as an opt-in integration because its published usage terms restrict automated use outside the documented open-source/research cases.
- [ ] Design a normalized external-security import layer for Semgrep, Gitleaks, pip-audit, OSV-Scanner, and RustSec outputs under a stable AigisCode schema.
- [ ] Extend the self-healing loop into external-finding triage so imported scanner results can also become actionable, accepted/suppressed, or informational instead of staying outside AI/rules feedback.
- [ ] Replace manual JSON parsing in AI review/security review with OpenAI Responses structured outputs as the primary path, keeping Codex CLI/Claude JSON prompting as fallbacks.
- [ ] Integration note from official docs: use JSON as the required ingest format across Gitleaks, pip-audit, OSV-Scanner, PHPStan, Ruff, Biome, and ESLint; prefer native SARIF only where officially supported (Gitleaks, OSV-Scanner, Ruff, Biome), and treat PHPStan/ESLint as JSON-first unless we add a converter or custom formatter layer.
- [ ] Define a language-agnostic architectural rule engine for YAGNI/overengineering/principles analysis with adapters for PHP, Python, JS/TS, Ruby, and Rust.
- [ ] Time-box or background heavy external analyzers like `phpstan` on large repos so fast security runs do not stall `analyze`.
- [ ] Normalize noisy tool stderr (for example Composer deprecation notices on PHP 8.5) before surfacing tool summaries in reports.
- [ ] Add timeouts and per-tool opt-in defaults for long-running external analyzers like PHPStan on very large repos.
- [ ] Calibrate and deduplicate the 243 imported Ruff security findings in `../newerp` before enabling broader external-tool defaults.
- [ ] Add CLI e2e coverage for `report --external-tool` / `report --run-ruff-security`, including unsupported, unavailable, and failed tool-run cases.
- [ ] Decide whether external findings should enter the rules/AI-review loop; today they are collected after Phase 2c, so they are reported but never triaged or suppressible through the existing review pipeline.
- [ ] Expand self-analysis coverage: run AigisCode against its own repo and codify any recurring architectural smells into rules/docs.
- [ ] Add architecture governance and enforcement guidance to `AGENTS.md`, including forbidden dependencies, exception process, and change-rejection criteria.
- [ ] Keep expanding `AGENTS.md` as the product constitution: generic core, pluggable analyzers, rule-engine-first design, and language -> framework -> platform support strategy.
- [ ] Codify Python architecture governance as executable contracts: Import Linter layer/forbidden/protected rules, Ruff banned APIs/imports, mypy strict module boundaries, and a dedicated exception registry with owners/expiry.
- [ ] Add architecture import-boundary tests so detector modules cannot depend directly on plugin dispatch/orchestration layers.
- [ ] Extract shared analysis/report assembly from `cli.py` so `analyze` and `report` stop duplicating the same orchestration flow.
- [ ] Extract a deterministic analysis-assembly service from `cli.py` for shared policy/plugin resolution, graph+detector execution, rules/external filtering, and `ReportData` construction, leaving Rich/Typer messaging in the CLI.
- [ ] Move semantic-envelope layer classification behind a dedicated boundary instead of importing graph heuristics directly into `workers/codex.py`.
- [ ] Replace `ReportData.dead_code/review/hardwiring` `Any` fields with typed report DTOs or protocols so report-layer boundaries are explicit and enforceable.
- [ ] Keep reducing self-analysis hardwiring noise in core modules (`rules/engine.py`, `graph/hardwiring.py`, `cli.py`, `policy/plugins.py`) before deciding what website styling literals should remain reportable.
- [ ] Address the remaining self-analysis Ruff security imports: dynamic SQL false positive in `graph/deadcode.py`, subprocess policy in `security/external.py`, and benign-IP handling in `graph/hardwiring.py`.

## In Progress

## Blocked

## Done
- [x] Add first-class Rust dead-code and hardwiring detection so Rust no longer shows up as detector partial coverage in standard AigisCode reports
- [x] Run a real AI-backed evaluation on `../zed`, capture the resulting report/handoff, and verify what AigisCode currently concludes about the Rust reference workspace
- [x] Add first-pass Rust symbol and dependency extraction so indexed `.rs` files contribute basic graph analysis and import coverage
- [x] Add `cargo-deny` as a first-class external analyzer for Rust advisories/licenses/bans/sources and normalize its findings into AigisCode reports
- [x] Add `cargo-clippy` as a first-class external analyzer for Rust workspaces
- [x] Record the current Rust coverage baseline for `../zed` (`1725` supported source files in the current clone) so future Rust audit work has a real regression target
- [x] Clone `zed` into `../zed` and assess current Rust audit coverage gaps for the whole AigisCode suite
- [x] Implement a backend-neutral session handoff/brief artifact in AigisCode reports and CLI outputs so AI agents can resume work without chat context
- [x] Evaluate whether the Claude Code limit-management workflow from the referenced dev.to article should be adopted in AigisCode or the website/app tooling
- [x] Investigate security-analysis flow, Codex SDK integration, reporting outputs, and concrete bugs in the end-to-end pipeline
- [x] Add first-class security analysis output and report sections for high-signal hardcoded network and env findings
- [x] Add per-run report archival so agent findings are preserved under `.aigiscode/reports/`
- [x] Add end-to-end coverage for `aigiscode analyze` security findings and archived report outputs
- [x] Align the JSON report contract with documented `graph_analysis.*` paths and expose `--output-dir` across commands for dedicated report locations
- [x] Implement normalized external-security ingestion and archived per-run findings for `../newerp`
- [x] Preserve unsupported `--external-tool` selections as explicit failed tool runs instead of silently dropping them during normalization
- [x] Make report archival collision-safe when two runs share the same second-level timestamp, so archived findings are never overwritten
- [x] Generate the security-remediation recommendation for external-only security findings, not just internal hardwiring findings
- [x] Keep external raw artifacts and archived report files under the same collision-safe run directory across same-second runs
- [x] Harden CLI e2e archive assertions so collision-safe per-run report directories are selected deterministically
- [x] Add strict architecture governance rules and self-audit the AigisCode codebase against them
- [x] Move semantic-envelope layer classification behind a dedicated boundary instead of importing graph heuristics directly into `workers/codex.py`
- [x] Treat non-zero external tool exits with zero normalized findings as failed runs instead of clean passes
- [x] Add explicit product-direction guidance to `AGENTS.md` for generic, pluggable, rules-based analyzer architecture
- [x] Extract shared deterministic analysis/report assembly from `cli.py` into an orchestration module
- [x] Fix `report` so `--plugin`, `--policy-file`, and `--plugin-module` actually flow into policy/runtime resolution
- [x] Stop `analyze` and `report` from reserving archive run directories before a real report run exists
- [x] Teach TS/TSX unused-import analysis to respect JSX component usage so React icon imports are not flagged as dead code
- [x] Fix website locale-loader typing so `npm run lint` succeeds with nested translation JSON payloads
- [x] Split `src/aigiscode/indexer/store.py` into focused repositories behind a thin store shell so self-analysis no longer flags it as a god class
- [x] Reduce detector-internal hardwiring noise in core modules enough to bring self-analysis from 81 to 76 hardwiring findings while keeping dead code and god classes at zero
- [x] Make the self-healing loop first-class in reports and policy by exposing actionable / accepted-by-policy / informational dispositions and AI-feedback counts
- [x] Revise README and architecture/agent docs so the self-healing loop, informational policy, and external-security lifecycle are consistent everywhere
- [x] Extend external security findings into the same typed AI review / rules feedback loop as native detector findings
- [x] Correct backend naming/selection so authenticated Codex CLI sessions are preferred when available and Python code stops pretending the Responses API path is the Codex SDK
