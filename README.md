# Buffett Earnings Toolkit

[English](README.md) | [简体中文](README.zh-CN.md)

Version 0.2.0 makes native Codex subagents the main research path. Python freezes inputs, validates research and commits reports; the parent Codex session assigns one subagent per selected company and independently reviews the results.

The research contract is **`buffett-research-v2.5`**, valuation policy **`buffett-valuation-6`**, and parent policy **`parent-assumption-tolerance-2026-11-v3`**. The evidence-based Buffett foundation retains its separate version 2.0. This project does not claim to reproduce Buffett's complete judgment.

## Setup

Use Python 3.11 or newer and run commands from a full repository checkout with an editable install. Keep documentation and skill references with the source: the runtime freezes those repository-local files. Running a standalone wheel without that source layout is not supported.

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The v2 research path uses the native Codex session and its available browsing/data tools. It requires neither `OPENAI_API_KEY` nor the Claude Agent SDK. `tzdata` supplies IANA time zones on Windows. `pandas` and `yfinance` support the separate calendar utility.

## Start a research run

Read [AGENTS.md](AGENTS.md) and the [v2 workflow](docs/buffett_v2/README.md) before research.

```bash
python -m src.tools.buffett_v2_pipeline universe
python -m src.tools.buffett_v2_pipeline context
```

`universe` starts from `data/company_pool.csv` and merges ticker identifiers from existing research, monitoring state and optional legacy outputs. Both `universe` and `prepare` accept `--universe-csv PATH`; use the same input for discovery and preparation. The pool supplies identifiers, not research authorization or prior valuations.

Unsupported or unsafe ticker rows are skipped and reported through `skipped_row_count`; inspect `inbox/universe.json` or the run's `scope.json` under `company_pool.skipped_rows` before accepting monitoring coverage. The source CSV remains unchanged.

For an explicitly requested company, prepare a manual run with its ticker and reason. For example, after the user requests Microsoft research:

```bash
python -m src.tools.buffett_v2_pipeline prepare --mode manual --tickers MSFT --reason "User requested Microsoft research"
```

Preparation returns a run ID and creates `outputs/buffett_v2/runs/<run-id>/`. It freezes the contract, policy, parent/subagent instructions, framework and runtime. It does not dispatch research itself.

Ask Codex in this repository to continue that run using native subagents. The parent dispatches only `plan.selected`, one company per subagent, in waves that fit available slots. Each subagent reads the frozen instructions, works only in its company directory and submits `research.json`, `research_report.md` and `metadata.json`. Its entry policy stays `pending_parent`; it cannot assign tiers or write parent decisions/reviews. If native subagents are unavailable, report that limitation instead of silently using a model API or pretending dispatch occurred.

The parent independently reviews sources, arithmetic, economic assumptions and risk gates; writes a hash-bound `parent_entry_decision.json`; and applies it:

```bash
python -m src.tools.buffett_v2_pipeline apply-entry --run-id <run-id> --ticker MSFT
```

After substantive review, the parent writes the required hash-bound `parent_review.json` and validates:

```bash
python -m src.tools.buffett_v2_pipeline validate --run-id <run-id> --tickers MSFT
```

See [parent instructions](docs/buffett_v2/PARENT_INSTRUCTIONS.md) for exact fields and ten review checks. Machine validation does not replace economic judgment. Any artifact change requires revalidation.

## Daily earnings and publication

Use `context` to fix a real UTC cutoff and New York date. Verify actual releases from primary sources across the complete universe; a calendar date or scheduled announcement is only a lead. Write a unique `inbox/<input-id>/events.json` and full-scope `checks.json`, then prepare:

```bash
python -m src.tools.buffett_v2_pipeline prepare --mode daily_earnings --cutoff <UTC-cutoff> --events outputs/buffett_v2/inbox/<input-id>/events.json --checks outputs/buffett_v2/inbox/<input-id>/checks.json --since <context.initial_scan_from>
```

Use `--since` only for the first daily run; later runs preserve per-company watermarks and completed-event deduplication. Older startup backlog remains pending unless authorized. Newly verified releases since the last successful check remain eligible across a New York date change. A policy upgrade, empty score or price change does not authorize historical research. The [workflow](docs/buffett_v2/README.md), [migration rules](docs/buffett_v2/MIGRATION.md) and [daily task prompt](docs/buffett_v2/automation_prompt.txt) define complete input and recovery rules. The prompt is reusable task text; it does not create a schedule.

After research acceptance, obtain completed-session quotes and prepare exact-scope `quotes.json`, `outcomes.json` and `quote_review.json` as documented. Research reviews use file-byte SHA256 hashes; quote review uses `pipeline.value_hash(quotes_object)`.

```bash
python -m src.tools.buffett_v2_pipeline publish --run-id <run-id> --quotes outputs/buffett_v2/inbox/<input-id>/quotes.json --outcomes outputs/buffett_v2/inbox/<input-id>/outcomes.json --quote-review outputs/buffett_v2/inbox/<input-id>/quote_review.json
python -m src.tools.buffett_v2_pipeline status
```

Publication validates artifacts and atomically changes `current.json` to an immutable generation. Incomplete new earnings research disables that company's prior price reference. Keep generated research, reports and caches under ignored `outputs/` or `.cache/`; do not edit production CSV/state by hand. Resume older runs with their frozen runtime, as described in the workflow.

## Economic model

Four independently supported economic paths produce `V_central`, `V_conservative`, `V_adverse` and `V_floor`, with the same currency, economic-share rights and discount rate. Adverse models one material thesis failure or at most two directly related failures. Floor is a low-state **going-concern economic floor**; liquidation is a separate recovery reference. Fixed discounts and rate substitutions cannot create the anchors.

The price tiers are `watch`, `research`, `starter`, `add` and `fat_pitch`. Evidence, survival and thesis-break gates separately constrain the research tier. A missing reliable floor cannot qualify as a fat pitch; all six fat-pitch checks require evidence. Research output always sets boolean `actionable=false` and does not authorize orders or position sizing.

## Calendar and legacy compatibility

The existing calendar utility remains available:

```bash
python -m src.tools.earnings_calendar refresh --limit 5
```

`src/tools/buffett_pipeline.py` is retained for legacy users and calendar helpers. Its local/GPT/skill/Batch analysis and old buy-zone outputs are outside the v2 workflow. Install `python -m pip install -e ".[legacy]"` only for those modes; optional API configuration is in `.env.example`. Legacy API modes may incur separate charges. V2 does not call Files, Batch, Responses or Agents research APIs, nor import old valuations, scores, buy zones or event-completion state; its legacy adapter reads ticker identifiers only.

## Tests and included data

```bash
python -m pytest
```

Tests use offline synthetic data for contract, gate, scheduling, validation and publication behavior. `data/company_pool.csv` contains public identifiers and classifications, without user accounts or research results. Current facts still require verification; Yahoo Finance calendar data may be incomplete.

## License

No open-source license has been selected. Add a `LICENSE` before publishing if explicit reuse and redistribution rights are intended.
