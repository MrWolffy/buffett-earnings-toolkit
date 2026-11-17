# Changelog

## 0.2.0 — 2026-11-17

- Make native Codex subagents the documented research workflow. Each selected
  company has an isolated research handoff; the parent independently adopts
  entry policy, reviews evidence, validates artifacts, and publishes.
- Adopt `buffett-research-v2.5`, `buffett-valuation-6`, and
  `parent-assumption-tolerance-2026-11-v3` from the current Investment research
  implementation. The underlying evidence-based Buffett framework retains its
  own version, 2.0, including its sources and revision history.
- Add central, conservative, adverse, and going-concern floor economic anchors,
  five price tiers, and separate evidence, survival, and thesis-break gates.
  Preserve the v2.5 distinction between going-concern floor support and
  liquidation references. Research tiers do not authorize orders or positions.
- Ship the complete deterministic runtime, frozen-run workflow, schema, policy,
  parent and subagent instructions, migration rules, and offline regressions.
- Bootstrap CLI monitoring from the bundled public company pool. Import only
  tickers, report unsupported identifiers, and keep monitoring scope separate
  from research authorization. Existing v1 outputs supply tickers only.
- Keep legacy pipeline and earnings-calendar commands available. Move the
  legacy research SDK dependencies to an optional installation extra. The new
  research path uses the host's native subagents and needs no research API key.
- Keep generated research, quotes, reports, caches, and credentials outside the
  distributable source. Never convert old valuations into new economic anchors
  or edit an existing frozen run to upgrade its contract.

## 0.1.0

- Initial toolkit with the public company pool, legacy analysis pipeline, and
  earnings-calendar utility.
