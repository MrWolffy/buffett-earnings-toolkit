"""All-company earnings calendar CLI.

This script keeps the earnings-date tracking separate from the Buffett
analysis pipeline. It reads the full ticker universe, refreshes the next
earnings date with yfinance, writes a reusable CSV calendar, and produces a
daily report of companies scheduled to report soon.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from src.tools import buffett_pipeline as bp


DEFAULT_CALENDAR_FILENAME = "earnings_calendar.csv"

EARNINGS_CALENDAR_FIELDS = [
    "ticker",
    "company_name",
    "sector",
    "industry",
    "source_roic",
    "next_earnings_date",
    "next_earnings_datetime",
    "next_earnings_session",
    "next_earnings_source",
    "has_option_expiring_within_7d",
    "next_option_expiration_date",
    "option_expirations_within_7d",
    "option_expiration_source",
    "last_earnings_date",
    "last_earnings_datetime",
    "last_earnings_has_actual",
    "checked_at",
    "data_status",
    "error",
]


class EarningsCalendarAdapter:
    def __init__(
        self,
        cache_dir: Path = bp.DEFAULT_CACHE_DIR,
        ticker_factory: Callable[[str], Any] | None = None,
        max_retries: int = 2,
        retry_sleep: float = 1.0,
        force_refresh: bool = False,
    ) -> None:
        self.cache_dir = cache_dir / "earnings_calendar"
        self.yfinance_cache_dir = cache_dir / "_yfinance"
        self.ticker_factory = ticker_factory
        self.max_retries = max_retries
        self.retry_sleep = retry_sleep
        self.force_refresh = force_refresh

    def _cache_path(self, ticker: str, as_of: str) -> Path:
        safe = ticker.replace("/", "_")
        return self.cache_dir / f"{safe}_{as_of}.json"

    def _ticker(self, ticker: str) -> Any:
        if self.ticker_factory:
            return self.ticker_factory(ticker)
        import yfinance as yf

        self._configure_yfinance_cache(yf)
        return yf.Ticker(ticker)

    def _configure_yfinance_cache(self, yf: Any) -> None:
        self.yfinance_cache_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location(str(self.yfinance_cache_dir))

    def get_earnings_calendar(self, ticker: str, as_of: str, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._cache_path(ticker, as_of)
        if path.exists() and not self.force_refresh:
            cached = json.loads(path.read_text(encoding="utf-8"))
            upcoming = bool(bp.earnings_within_days([cached], as_of, 3))
            if cached.get("data_status") != "fetch_error" and (
                not upcoming or row_has_current_confirmation(cached, as_of)
            ):
                return cached

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                row = self._fetch_earnings_calendar(ticker, as_of, seed or {})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(row, indent=2, default=bp.json_default), encoding="utf-8")
                return row
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_sleep * (attempt + 1))

        if seed and bp.parse_date(seed.get("next_earnings_date")):
            return {
                **seed,
                "ticker": ticker,
                **self._option_fields_from_seed(seed),
                "checked_at": as_of,
                "data_status": seed.get("data_status") or "fetch_error",
                "error": last_error,
            }

        return {
            **(seed or {}),
            "ticker": ticker,
            "next_earnings_date": "",
            "next_earnings_datetime": "",
            "next_earnings_session": "unknown",
            "next_earnings_source": "missing",
            "has_option_expiring_within_7d": "",
            "next_option_expiration_date": "",
            "option_expirations_within_7d": "",
            "option_expiration_source": "",
            "last_earnings_date": (seed or {}).get("last_earnings_date", ""),
            "last_earnings_datetime": (seed or {}).get("last_earnings_datetime", ""),
            "last_earnings_has_actual": (seed or {}).get("last_earnings_has_actual", ""),
            "checked_at": as_of,
            "data_status": "fetch_error",
            "error": last_error,
        }

    def _fetch_earnings_calendar(self, ticker: str, as_of: str, seed: dict[str, Any]) -> dict[str, Any]:
        stock = self._ticker(ticker)
        earnings_dates = self._safe_value(lambda: stock.get_earnings_dates(limit=16))
        earnings = bp.earnings_info_from_frame(earnings_dates, as_of)
        records = earnings.get("earnings_dates") or []
        if not records and bp.parse_date(seed.get("next_earnings_date")):
            option_fields = (
                self._fetch_option_expiration_info(stock, as_of)
                if bp.earnings_within_days([seed], as_of, 3)
                else self._option_fields_from_seed(seed)
            )
            option_fields = self._preserve_seed_options_on_fetch_error(seed, option_fields)
            return {
                **seed,
                "ticker": ticker,
                "next_earnings_datetime": seed.get("next_earnings_datetime", ""),
                "next_earnings_session": seed.get("next_earnings_session") or "unknown",
                **option_fields,
                "checked_at": as_of,
                "data_status": seed.get("data_status") or "missing_earnings_calendar",
                "error": "yfinance returned no earnings dates; preserved existing calendar row",
            }
        status = "ok" if records else "missing_earnings_calendar"
        upcoming = bool(
            bp.earnings_within_days(
                [{"next_earnings_date": earnings["next_earnings_date"]}],
                as_of,
                3,
            )
        )
        option_fields = self._fetch_option_expiration_info(stock, as_of) if upcoming else self._option_fields_from_seed(seed)
        option_fields = self._preserve_seed_options_on_fetch_error(seed, option_fields)
        return {
            **seed,
            "ticker": ticker,
            "next_earnings_date": earnings["next_earnings_date"],
            "next_earnings_datetime": earnings["next_earnings_datetime"] if upcoming else "",
            "next_earnings_session": earnings["next_earnings_session"] if upcoming else "",
            "next_earnings_source": earnings["next_earnings_source"],
            **option_fields,
            "last_earnings_date": earnings["last_earnings_date"],
            "last_earnings_datetime": earnings["last_earnings_datetime"],
            "last_earnings_has_actual": earnings["last_earnings_has_actual"],
            "checked_at": as_of,
            "data_status": status,
            "error": "",
        }

    @staticmethod
    def _safe_value(func: Callable[[], Any]) -> Any:
        try:
            return func()
        except Exception:
            return None

    def _fetch_option_expiration_info(self, stock: Any, as_of: str) -> dict[str, Any]:
        try:
            expirations = stock.options
        except Exception:
            return {
                "has_option_expiring_within_7d": "",
                "next_option_expiration_date": "",
                "option_expirations_within_7d": "",
                "option_expiration_source": "fetch_error",
            }
        info = bp.option_expiration_info(expirations, as_of)
        return {
            **info,
            "option_expiration_source": "yfinance" if expirations is not None else "missing",
        }

    @staticmethod
    def _option_fields_from_seed(seed: dict[str, Any] | None) -> dict[str, Any]:
        seed = seed or {}
        return {
            "has_option_expiring_within_7d": seed.get("has_option_expiring_within_7d", ""),
            "next_option_expiration_date": seed.get("next_option_expiration_date", ""),
            "option_expirations_within_7d": seed.get("option_expirations_within_7d", ""),
            "option_expiration_source": seed.get("option_expiration_source", ""),
        }

    def _preserve_seed_options_on_fetch_error(
        self,
        seed: dict[str, Any] | None,
        option_fields: dict[str, Any],
    ) -> dict[str, Any]:
        if option_fields.get("option_expiration_source") != "fetch_error":
            return option_fields
        seed_options = self._option_fields_from_seed(seed)
        seed_value = seed_options.get("has_option_expiring_within_7d")
        seed_text = str(seed_value).strip().lower()
        if seed_value is True or seed_value is False or seed_text in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
            return seed_options
        return option_fields


def calendar_path(output_dir: Path) -> Path:
    return output_dir / DEFAULT_CALENDAR_FILENAME


def load_calendar_rows(output_dir: Path) -> list[dict[str, str]]:
    return bp.read_csv_rows(calendar_path(output_dir))


def load_calendar_universe(output_dir: Path, tickers_csv: Path) -> list[dict[str, str]]:
    universe_path = output_dir / "ticker_universe.csv"
    if universe_path.exists():
        return bp.read_csv_rows(universe_path)
    return bp.load_ticker_universe(tickers_csv)


def calendar_row_needs_refresh(
    row: dict[str, Any],
    as_of: str,
    refresh_mode: str = "due",
    max_age_days: int = 1,
) -> bool:
    if refresh_mode == "all":
        return True

    target = bp.parse_date(as_of) or date.today()
    next_earnings = bp.parse_date(row.get("next_earnings_date"))
    if next_earnings is None or next_earnings < target:
        return True

    if refresh_mode == "due":
        return False

    checked_at = bp.parse_date(row.get("checked_at"))
    if checked_at is None:
        return True
    return (target - checked_at).days >= max(0, max_age_days)


def merged_calendar_base(
    universe_rows: Iterable[dict[str, Any]],
    existing_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    universe_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in universe_rows
        if str(row.get("ticker", "")).strip()
    }
    existing_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in existing_rows
        if str(row.get("ticker", "")).strip()
    }
    tickers = sorted(set(universe_by_ticker) | set(existing_by_ticker))
    return [
        {**universe_by_ticker.get(ticker, {}), **existing_by_ticker.get(ticker, {}), "ticker": ticker}
        for ticker in tickers
    ]


def merge_refreshed_calendar_rows(
    base_rows: Iterable[dict[str, Any]],
    refreshed_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = {
        str(row.get("ticker", "")).upper(): row
        for row in base_rows
        if str(row.get("ticker", "")).strip()
    }
    for row in refreshed_rows:
        ticker = str(row.get("ticker", "")).upper()
        if ticker:
            merged[ticker] = {**merged.get(ticker, {}), **row, "ticker": ticker}
    return [merged[ticker] for ticker in sorted(merged)]


def row_has_current_confirmation(row: dict[str, Any], as_of: str) -> bool:
    if str(row.get("checked_at") or "").strip() != as_of:
        return False
    session = str(row.get("next_earnings_session") or "").strip()
    option_value = row.get("has_option_expiring_within_7d")
    option_text = str(option_value).strip().lower()
    session_confirmed = session in {"before_market", "after_market", "during_market", "unknown"}
    option_confirmed = option_value is True or option_value is False or option_text in {
        "true",
        "false",
        "1",
        "0",
        "yes",
        "no",
        "y",
        "n",
    }
    return session_confirmed and option_confirmed


def unconfirmed_upcoming_earnings(rows: list[dict[str, Any]], as_of: str, days: int = 3) -> list[dict[str, Any]]:
    return [
        row
        for row in bp.earnings_within_days(rows, as_of, days)
        if not row_has_current_confirmation(row, as_of)
    ]


def confirm_upcoming_earnings_sessions(
    rows: list[dict[str, Any]],
    adapter: EarningsCalendarAdapter,
    as_of: str,
    days: int = 3,
    checkpoint_path: Path | None = None,
    checkpoint_interval: int = 100,
    confirmation_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pending = unconfirmed_upcoming_earnings(rows, as_of, days)
    upcoming = pending[:confirmation_limit] if confirmation_limit and confirmation_limit > 0 else pending
    confirmed: list[dict[str, Any]] = []
    checkpoint_interval = max(1, checkpoint_interval)
    merged_rows = rows
    for index, row in enumerate(upcoming, start=1):
        ticker = str(row.get("ticker", "")).upper()
        confirmed.append(adapter.get_earnings_calendar(ticker, as_of, row))
        print(f"Confirmed {index}/{len(upcoming)} {ticker}: session/options")
        if checkpoint_path and index % checkpoint_interval == 0:
            merged_rows = merge_refreshed_calendar_rows(rows, confirmed)
            bp.write_csv(checkpoint_path, merged_rows, EARNINGS_CALENDAR_FIELDS)
    merged_rows = merge_refreshed_calendar_rows(rows, confirmed)
    if checkpoint_path:
        bp.write_csv(checkpoint_path, merged_rows, EARNINGS_CALENDAR_FIELDS)
    return merged_rows, confirmed


def update_checkpoint_for_remaining_confirmations(
    rows: list[dict[str, Any]],
    checkpoint_path: Path,
    as_of: str,
    days: int,
) -> int:
    remaining = len(unconfirmed_upcoming_earnings(rows, as_of, days))
    if remaining:
        bp.write_csv(checkpoint_path, rows, EARNINGS_CALENDAR_FIELDS)
    else:
        checkpoint_path.unlink(missing_ok=True)
    return remaining


def refresh_earnings_calendar(
    universe_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    adapter: EarningsCalendarAdapter,
    as_of: str,
    refresh_mode: str = "due",
    max_age_days: int = 1,
    limit: int | None = None,
    tickers: list[str] | None = None,
    start: int = 1,
    end: int | None = None,
    checkpoint_path: Path | None = None,
    checkpoint_interval: int = 100,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_rows = merged_calendar_base(universe_rows, existing_rows)
    candidates = [
        row
        for row in base_rows
        if calendar_row_needs_refresh(row, as_of, refresh_mode=refresh_mode, max_age_days=max_age_days)
    ]
    rows_to_refresh = bp.iter_limited(candidates, limit, tickers, start, end)
    refreshed: list[dict[str, Any]] = []
    checkpoint_interval = max(1, checkpoint_interval)

    for index, row in enumerate(rows_to_refresh, start=1):
        ticker = str(row["ticker"]).upper()
        refreshed_row = adapter.get_earnings_calendar(ticker, as_of, row)
        refreshed.append(refreshed_row)
        print(
            f"Refreshed {index}/{len(rows_to_refresh)} {ticker}: "
            f"next={refreshed_row.get('next_earnings_date') or 'missing'}"
        )
        if checkpoint_path and index % checkpoint_interval == 0:
            bp.write_csv(
                checkpoint_path,
                merge_refreshed_calendar_rows(base_rows, refreshed),
                EARNINGS_CALENDAR_FIELDS,
            )

    merged = merge_refreshed_calendar_rows(base_rows, refreshed)
    if checkpoint_path:
        bp.write_csv(checkpoint_path, merged, EARNINGS_CALENDAR_FIELDS)
    return merged, refreshed


def sync_good_companies_from_calendar(output_dir: Path, calendar_rows: list[dict[str, Any]]) -> int:
    good_rows = bp.load_good_company_rows(output_dir)
    if not good_rows:
        return 0

    calendar_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in calendar_rows
        if str(row.get("ticker", "")).strip()
    }
    updated: list[dict[str, Any]] = []
    changed = 0
    for row in good_rows:
        ticker = str(row.get("ticker", "")).upper()
        calendar_row = calendar_by_ticker.get(ticker)
        if not calendar_row:
            updated.append(row)
            continue
        replacement = {
            **row,
            "next_earnings_date": calendar_row.get("next_earnings_date") or row.get("next_earnings_date", ""),
            "next_earnings_source": calendar_row.get("next_earnings_source") or row.get("next_earnings_source", "missing"),
            "last_earnings_date": calendar_row.get("last_earnings_date") or row.get("last_earnings_date", ""),
        }
        if replacement != row:
            changed += 1
        updated.append(replacement)

    bp.write_csv(output_dir / "good_companies.csv", updated, bp.GOOD_COMPANIES_FIELDS)
    return changed


def write_earnings_daily_report(path: Path, rows: list[dict[str, Any]], as_of: str, days: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    upcoming = bp.earnings_within_days(rows, as_of, days)
    missing = [row for row in rows if not bp.parse_date(row.get("next_earnings_date"))]
    lines = [
        f"# Earnings Calendar - {as_of}",
        "",
        f"- Companies tracked: {len(rows)}",
        f"- Earnings in next {days} days: {len(upcoming)}",
        f"- Missing next earnings date: {len(missing)}",
        "",
        f"## Earnings In Next {days} Days",
        "",
    ]
    if upcoming:
        lines.append("| Ticker | Company | Earnings Date | Session | Options <=7d | Source | Last Checked |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in upcoming:
            lines.append(
                f"| {row.get('ticker', '')} | {row.get('company_name', '')} | "
                f"{row.get('next_earnings_date', '')} | "
                f"{bp.format_earnings_session(row.get('next_earnings_session'))} | "
                f"{bp.format_option_expiration_status(row)} | "
                f"{row.get('next_earnings_source', '')} | "
                f"{row.get('checked_at', '')} |"
            )
    else:
        lines.append(f"No tracked companies have earnings scheduled in the next {days} days.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def command_refresh(args: argparse.Namespace) -> int:
    universe_rows = load_calendar_universe(args.output_dir, args.tickers_csv)
    checkpoint_path = args.output_dir / f".{DEFAULT_CALENDAR_FILENAME}.partial"
    existing_rows = bp.read_csv_rows(checkpoint_path) if checkpoint_path.exists() else load_calendar_rows(args.output_dir)
    adapter = EarningsCalendarAdapter(
        args.cache_dir,
        max_retries=args.max_retries,
        force_refresh=args.force_refresh,
    )
    rows, refreshed = refresh_earnings_calendar(
        universe_rows,
        existing_rows,
        adapter,
        args.as_of,
        refresh_mode=args.refresh_mode,
        max_age_days=args.max_age_days,
        limit=args.limit,
        tickers=args.tickers,
        start=args.start,
        end=args.end,
        checkpoint_path=checkpoint_path,
        checkpoint_interval=args.checkpoint_interval,
    )
    rows, session_confirmed = confirm_upcoming_earnings_sessions(
        rows,
        adapter,
        args.as_of,
        args.report_days,
        checkpoint_path=checkpoint_path,
        checkpoint_interval=args.checkpoint_interval,
        confirmation_limit=args.confirmation_limit,
    )

    output_path = calendar_path(args.output_dir)
    bp.write_csv(output_path, rows, EARNINGS_CALENDAR_FIELDS)
    report_path = args.output_dir / "daily" / f"{args.as_of}_earnings.md"
    write_earnings_daily_report(report_path, rows, args.as_of, args.report_days)
    synced = sync_good_companies_from_calendar(args.output_dir, rows)
    remaining = update_checkpoint_for_remaining_confirmations(rows, checkpoint_path, args.as_of, args.report_days)

    print(f"Wrote {len(rows)} tracked companies to {output_path}")
    print(f"Refreshed {len(refreshed)} companies")
    print(f"Confirmed earnings sessions for {len(session_confirmed)} companies")
    print(f"Remaining unconfirmed upcoming earnings rows: {remaining}")
    print(f"Synced earnings fields for {synced} Buffett good-company rows")
    print(f"Wrote earnings daily report to {report_path}")
    return 0


def command_report(args: argparse.Namespace) -> int:
    checkpoint_path = args.output_dir / f".{DEFAULT_CALENDAR_FILENAME}.partial"
    rows = bp.read_csv_rows(checkpoint_path) if checkpoint_path.exists() else load_calendar_rows(args.output_dir)
    adapter = EarningsCalendarAdapter(
        args.cache_dir,
        max_retries=args.max_retries,
        force_refresh=args.force_refresh,
    )
    rows, confirmed = confirm_upcoming_earnings_sessions(
        rows,
        adapter,
        args.as_of,
        args.report_days,
        checkpoint_path=checkpoint_path,
        checkpoint_interval=args.checkpoint_interval,
        confirmation_limit=args.confirmation_limit,
    )
    bp.write_csv(calendar_path(args.output_dir), rows, EARNINGS_CALENDAR_FIELDS)
    report_path = args.output_dir / "daily" / f"{args.as_of}_earnings.md"
    write_earnings_daily_report(report_path, rows, args.as_of, args.report_days)
    remaining = update_checkpoint_for_remaining_confirmations(rows, checkpoint_path, args.as_of, args.report_days)
    print(f"Confirmed earnings sessions for {len(confirmed)} companies")
    print(f"Remaining unconfirmed upcoming earnings rows: {remaining}")
    print(f"Wrote earnings daily report to {report_path}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Calendar date in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", type=Path, default=bp.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=bp.DEFAULT_CACHE_DIR)
    parser.add_argument("--tickers", nargs="*", help="Optional ticker subset.")
    parser.add_argument("--start", type=int, default=1, help="1-based start row in the selected refresh list.")
    parser.add_argument("--end", type=int, default=None, help="1-based inclusive end row in the selected refresh list.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max row count for smoke runs.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh and report all-company earnings dates.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser("refresh")
    add_common_args(refresh)
    refresh.add_argument("--tickers-csv", type=Path, default=bp.DEFAULT_TICKERS_CSV)
    refresh.add_argument("--max-retries", type=int, default=2)
    refresh.add_argument("--force-refresh", action="store_true")
    refresh.add_argument("--refresh-mode", choices=["stale", "due", "all"], default="due")
    refresh.add_argument("--max-age-days", type=int, default=1)
    refresh.add_argument("--checkpoint-interval", type=int, default=100)
    refresh.add_argument("--confirmation-limit", type=int, default=200)
    refresh.add_argument("--report-days", type=int, default=3)
    refresh.set_defaults(func=command_refresh)

    report = subparsers.add_parser("report")
    report.add_argument("--as-of", default=date.today().isoformat(), help="Calendar date in YYYY-MM-DD format.")
    report.add_argument("--output-dir", type=Path, default=bp.DEFAULT_OUTPUT_DIR)
    report.add_argument("--cache-dir", type=Path, default=bp.DEFAULT_CACHE_DIR)
    report.add_argument("--max-retries", type=int, default=2)
    report.add_argument("--force-refresh", action="store_true")
    report.add_argument("--report-days", type=int, default=3)
    report.add_argument("--checkpoint-interval", type=int, default=100)
    report.add_argument("--confirmation-limit", type=int, default=200)
    report.set_defaults(func=command_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
