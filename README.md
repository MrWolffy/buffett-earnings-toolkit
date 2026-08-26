# Buffett Earnings Toolkit

[English](README.md) | [简体中文](README.zh-CN.md)

A Python toolkit for screening a public company universe, producing Buffett-style company analyses, and maintaining an earnings calendar with `yfinance`.

The repository includes the reusable source code, tests, Buffett analysis skill, and public company pool. Generated analyses, calendars, reports, logs, checkpoints, caches, and credentials are intentionally excluded from version control.

## Included components

- `.agents/skills/buffett/`: Buffett investment-analysis instructions and references.
- `src/tools/buffett_pipeline.py`: watchlist construction, company analysis, buy-zone monitoring, and OpenAI batch workflows.
- `src/tools/earnings_calendar.py`: earnings-date refresh and daily reporting.
- `data/company_pool.csv`: public input universe used by both tools. It contains only ticker, company name, sector, and industry; calculated screening and valuation results are excluded.
- `tests/`: offline unit tests for the pipeline and calendar logic.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

For GPT or skill analysis modes, copy `.env.example` to `.env` and set `OPENAI_API_KEY`. Never commit `.env`. Skill mode also uses the bundled `.agents/skills/buffett/` instructions through the Claude Agent SDK.

## Usage

Run an offline dependency check:

```bash
python -m src.tools.buffett_pipeline preflight --analysis-mode local
```

Build the watchlist from the included company pool:

```bash
python -m src.tools.buffett_pipeline build-watchlist
```

Run a small local analysis without calling OpenAI:

```bash
python -m src.tools.buffett_pipeline analyze-watchlist --analysis-mode local --limit 5
```

Refresh a small portion of the earnings calendar:

```bash
python -m src.tools.earnings_calendar refresh --limit 5
```

By default, generated files are written beneath `outputs/` and caches beneath `.cache/`. Both directories are ignored by Git.

## Tests

```bash
python -m pytest
```

## Data and API notes

Market and earnings data are fetched from Yahoo Finance through `yfinance`. OpenAI-backed modes may incur API charges and can produce variable model output. Review upstream data-provider and API terms before redistribution or production use.

## License

No open-source license has been selected yet. Add a `LICENSE` file before publishing if you want others to have explicit rights to use, modify, and redistribute the project.
