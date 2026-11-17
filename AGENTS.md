# Buffett Earnings Toolkit agent instructions

## Scope

For investment research and earnings monitoring, use the native Codex subagent workflow with `buffett-research-v2.5`, `buffett-valuation-6` and `parent-assumption-tolerance-2026-11-v3`. The foundation at `.agents/skills/buffett/SKILL.md` retains its separate 2.0 version. Read that skill and relevant references, then read `docs/buffett_v2/README.md`, `SUBAGENT_INSTRUCTIONS.md`, `PARENT_INSTRUCTIONS.md` and `MIGRATION.md` before research. Once a run exists, its frozen files control that run.

These research rules do not require company research or subagents for ordinary code/documentation maintenance. Preserve existing user changes and run relevant offline tests for code changes.

## Parent and subagent responsibilities

- Discover the complete monitoring universe with `universe`, using the same `--universe-csv` input during `prepare`. The bundled pool is a ticker source, not authorization to research every company. Verify actual earnings releases from primary sources and fix a real cutoff before daily preparation. Manual runs require explicit user tickers and a reason.
- The parent must actually dispatch native Codex subagents, one per company in `plan.selected`, using available concurrency slots in waves and inheriting the task model. Do not dispatch deferred companies or expand scope because a policy/skill changed. If native subagents are unavailable, report the limitation; do not substitute a model API or claim dispatch occurred.
- Each subagent reads the run's manifest, plan, frozen schema, policy, framework and `SUBAGENT_INSTRUCTIONS.md`; it writes only `runs/<run-id>/companies/<ticker>/`. Deliver `research.json`, `research_report.md` with an identical final JSON block, and `metadata.json` with route `codex_subagent` and task_scope `standalone_screen`.
- Subagent entry policy remains pending_parent, policy_id=null, defined_by=unassigned, boundaries/tiers empty and gates=null. Subagents cannot assign economic/actionable tiers or write `parent_entry_decision.json` or `parent_review.json`.
- The parent independently examines sources, cash bridges, four-anchor economics, independent checks and survival/evidence gates. Write the hash-bound parent decision, run `apply-entry`, conduct all ten substantive parent checks, write `parent_review.json` and run `validate`. Do not automatically fill checks as passed. Any artifact modification requires new hashes and revalidation.
- Publish accepted results with exact-scope outcomes, completed-session quotes and a substantive quote review. Quote review uses `pipeline.value_hash(quotes_object)`; research review uses file-byte SHA256. Verify `status` and the `current.json` generation after publication.

## Research boundaries

Python provides deterministic calculations, input freezing, validation and publication. Do not invoke OpenAI Files/Batch/Responses/Agents APIs or Claude Agent SDK as a substitute for native research subagents. The retained `buffett_pipeline.py` is a legacy compatibility component; do not use it as the v2 research runner. Calendar dates are leads rather than proof of release.

Construct central/conservative/adverse/floor from economic assumptions at one discount rate, currency and economic-share basis. Adverse permits one failed thesis or at most two directly causally linked failures. Floor describes a viable going concern; liquidation is a separate recovery reference. Follow all v2.5 independence, contradiction-resolution and six fat-pitch checks. Ordinary uncertainty is not an automatic veto. Missing floor does not permit fat_pitch. Boolean actionable remains false.

The standalone research task does not read user accounts, assign position sizes or execute trades. Follow the foundation skill's account-read workflow only when the user separately requests an actual portfolio opportunity-cost comparison. Research never grants trading authorization.

## Storage and verification

Keep research, raw evidence, quotes, reports and state under `outputs/buffett_v2/`; keep credentials out of artifacts. Read legacy `outputs/buffett/` only through the ticker adapter; never import its valuations, scores, price zones, success flags or completed-event state. Preserve immutable publications and frozen runs. Resume old runs using their own frozen runtime, and never hand-edit production CSV/state.

Run `python -m pytest` for the offline suite. CLI `context` and an isolated synthetic `prepare` can check portability without requesting real research. Do not fetch live data or start broad company research merely to validate a code update.
