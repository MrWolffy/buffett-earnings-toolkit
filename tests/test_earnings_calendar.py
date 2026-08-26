import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.tools import buffett_pipeline as bp
from src.tools import earnings_calendar as ec


class FakeEarningsTicker:
    def __init__(self, ticker):
        self.ticker = ticker
        self.options = ("2026-05-29", "2026-06-12")

    def get_earnings_dates(self, limit=16):
        return pd.DataFrame(
            {"Reported EPS": [1.0, None]},
            index=pd.DatetimeIndex(
                ["2026-05-20 16:00:00", "2026-05-29 08:00:00"],
                tz="America/New_York",
            ),
        )


class FakeEmptyEarningsTicker:
    def get_earnings_dates(self, limit=16):
        return pd.DataFrame()


class FakeEmptyEarningsAndBrokenOptionsTicker:
    @property
    def options(self):
        raise RuntimeError("options unavailable")

    def get_earnings_dates(self, limit=16):
        return pd.DataFrame()


class EarningsCalendarTests(unittest.TestCase):
    def test_adapter_fetches_earnings_only_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ec.EarningsCalendarAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeEarningsTicker(ticker),
                max_retries=0,
            )

            row = adapter.get_earnings_calendar("AAA", "2026-05-27", {"company_name": "AAA Inc."})

        self.assertEqual(row["ticker"], "AAA")
        self.assertEqual(row["company_name"], "AAA Inc.")
        self.assertEqual(row["next_earnings_date"], "2026-05-29")
        self.assertEqual(row["next_earnings_session"], "before_market")
        self.assertEqual(row["has_option_expiring_within_7d"], True)
        self.assertEqual(row["next_option_expiration_date"], "2026-05-29")
        self.assertEqual(row["last_earnings_date"], "2026-05-20")
        self.assertEqual(row["data_status"], "ok")

    def test_adapter_preserves_existing_date_when_refresh_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ec.EarningsCalendarAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeEmptyEarningsTicker(),
                max_retries=0,
                force_refresh=True,
            )

            row = adapter.get_earnings_calendar(
                "AAA",
                "2026-05-27",
                {
                    "ticker": "AAA",
                    "company_name": "AAA Inc.",
                    "next_earnings_date": "2026-05-29",
                    "next_earnings_datetime": "2026-05-29T08:00:00-04:00",
                    "next_earnings_session": "before_market",
                    "next_earnings_source": "yfinance",
                    "data_status": "ok",
                },
            )

        self.assertEqual(row["next_earnings_date"], "2026-05-29")
        self.assertEqual(row["next_earnings_session"], "before_market")
        self.assertEqual(row["data_status"], "ok")
        self.assertIn("preserved existing calendar row", row["error"])

    def test_adapter_marks_missing_preserved_session_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ec.EarningsCalendarAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeEmptyEarningsTicker(),
                max_retries=0,
                force_refresh=True,
            )

            row = adapter.get_earnings_calendar(
                "AAA",
                "2026-05-27",
                {
                    "ticker": "AAA",
                    "next_earnings_date": "2026-05-29",
                    "next_earnings_session": "",
                    "has_option_expiring_within_7d": "False",
                    "option_expiration_source": "yfinance",
                    "data_status": "ok",
                },
            )

        self.assertEqual(row["next_earnings_session"], "unknown")

    def test_adapter_does_not_overwrite_confirmed_options_on_retry_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ec.EarningsCalendarAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeEmptyEarningsAndBrokenOptionsTicker(),
                max_retries=0,
                force_refresh=True,
            )

            row = adapter.get_earnings_calendar(
                "AAA",
                "2026-05-27",
                {
                    "ticker": "AAA",
                    "next_earnings_date": "2026-05-29",
                    "has_option_expiring_within_7d": "False",
                    "next_option_expiration_date": "",
                    "option_expirations_within_7d": "",
                    "option_expiration_source": "yfinance",
                    "data_status": "ok",
                },
            )

        self.assertEqual(row["has_option_expiring_within_7d"], "False")
        self.assertEqual(row["option_expiration_source"], "yfinance")

    def test_refresh_merges_full_universe_and_existing_calendar(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ec.EarningsCalendarAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeEarningsTicker(ticker),
                max_retries=0,
            )
            universe = [
                {"ticker": "AAA", "company_name": "AAA Inc."},
                {"ticker": "BBB", "company_name": "BBB Inc."},
            ]
            existing = [{"ticker": "BBB", "company_name": "Old BBB", "next_earnings_date": "2026-08-01", "checked_at": "2026-05-27"}]

            rows, refreshed = ec.refresh_earnings_calendar(
                universe,
                existing,
                adapter,
                "2026-05-27",
                refresh_mode="all",
            )

        self.assertEqual([row["ticker"] for row in rows], ["AAA", "BBB"])
        self.assertEqual(len(refreshed), 2)
        self.assertEqual(rows[1]["company_name"], "Old BBB")
        self.assertEqual(rows[1]["next_earnings_date"], "2026-05-29")

    def test_calendar_row_refresh_policy_covers_missing_due_and_stale(self):
        self.assertTrue(ec.calendar_row_needs_refresh({"next_earnings_date": ""}, "2026-05-27"))
        self.assertTrue(ec.calendar_row_needs_refresh({"next_earnings_date": "2026-05-26"}, "2026-05-27"))
        self.assertFalse(
            ec.calendar_row_needs_refresh(
                {"next_earnings_date": "2026-08-01", "checked_at": "2026-05-26"},
                "2026-05-27",
            )
        )
        self.assertTrue(
            ec.calendar_row_needs_refresh(
                {"next_earnings_date": "2026-08-01", "checked_at": "2026-05-26"},
                "2026-05-27",
                refresh_mode="stale",
                max_age_days=1,
            )
        )
        self.assertFalse(
            ec.calendar_row_needs_refresh(
                {"next_earnings_date": "2026-08-01", "checked_at": "2026-05-27"},
                "2026-05-27",
            )
        )

    def test_command_refresh_reads_full_ticker_csv_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tickers_csv = root / "tickers.csv"
            output_dir = root / "buffett"
            cache_dir = root / "cache"
            with tickers_csv.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["ticker", "company_name", "sector", "industry", "roic"])
                writer.writeheader()
                writer.writerow({"ticker": "AAA", "company_name": "AAA Inc.", "sector": "Tech", "industry": "Software", "roic": "0.2"})
                writer.writerow({"ticker": "BBB", "company_name": "BBB Inc.", "sector": "Health", "industry": "Tools", "roic": "0.1"})

            original_adapter = ec.EarningsCalendarAdapter

            class TestAdapter(original_adapter):
                def __init__(self, *args, **kwargs):
                    super().__init__(
                        cache_dir=cache_dir,
                        ticker_factory=lambda ticker: FakeEarningsTicker(ticker),
                        max_retries=0,
                    )

            ec.EarningsCalendarAdapter = TestAdapter
            try:
                code = ec.main(
                    [
                        "refresh",
                        "--as-of",
                        "2026-05-27",
                        "--tickers-csv",
                        str(tickers_csv),
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--refresh-mode",
                        "all",
                    ]
                )
            finally:
                ec.EarningsCalendarAdapter = original_adapter

            self.assertEqual(code, 0)
            rows = bp.read_csv_rows(output_dir / "earnings_calendar.csv")
            self.assertEqual([row["ticker"] for row in rows], ["AAA", "BBB"])
            report = (output_dir / "daily" / "2026-05-27_earnings.md").read_text(encoding="utf-8")
            self.assertIn("Companies tracked: 2", report)
            self.assertIn("| AAA | AAA Inc. | 2026-05-29 | 盘前 | Yes (2026-05-29) | yfinance | 2026-05-27 |", report)

    def test_confirms_sessions_only_for_earnings_within_three_days(self):
        class RecordingAdapter:
            def __init__(self):
                self.tickers = []

            def get_earnings_calendar(self, ticker, as_of, seed):
                self.tickers.append(ticker)
                return {
                    **seed,
                    "next_earnings_datetime": "2026-05-29T16:00:00-04:00",
                    "next_earnings_session": "after_market",
                }

        rows = [
            {"ticker": "AAA", "next_earnings_date": "2026-05-29"},
            {"ticker": "BBB", "next_earnings_date": "2026-06-10"},
        ]
        adapter = RecordingAdapter()

        merged, confirmed = ec.confirm_upcoming_earnings_sessions(rows, adapter, "2026-05-27", 3)

        self.assertEqual(adapter.tickers, ["AAA"])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(merged[0]["next_earnings_session"], "after_market")

    def test_confirm_upcoming_writes_checkpoint_during_confirmation(self):
        class RecordingAdapter:
            def get_earnings_calendar(self, ticker, as_of, seed):
                return {
                    **seed,
                    "next_earnings_session": "before_market",
                    "has_option_expiring_within_7d": False,
                }

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / ".earnings_calendar.csv.partial"
            rows = [
                {"ticker": "AAA", "next_earnings_date": "2026-05-28"},
                {"ticker": "BBB", "next_earnings_date": "2026-05-29"},
            ]

            merged, confirmed = ec.confirm_upcoming_earnings_sessions(
                rows,
                RecordingAdapter(),
                "2026-05-27",
                3,
                checkpoint_path=checkpoint,
                checkpoint_interval=1,
            )

            checkpoint_rows = bp.read_csv_rows(checkpoint)

        self.assertEqual(len(confirmed), 2)
        self.assertEqual(len(checkpoint_rows), 2)
        self.assertEqual(merged[0]["next_earnings_session"], "before_market")

    def test_confirm_upcoming_skips_rows_already_confirmed_for_same_date(self):
        class RecordingAdapter:
            def __init__(self):
                self.tickers = []

            def get_earnings_calendar(self, ticker, as_of, seed):
                self.tickers.append(ticker)
                return {**seed, "next_earnings_session": "before_market"}

        rows = [
            {
                "ticker": "AAA",
                "next_earnings_date": "2026-05-28",
                "next_earnings_session": "after_market",
                "has_option_expiring_within_7d": "False",
                "option_expiration_source": "yfinance",
                "checked_at": "2026-05-27",
            },
            {"ticker": "BBB", "next_earnings_date": "2026-05-29"},
        ]
        adapter = RecordingAdapter()

        merged, confirmed = ec.confirm_upcoming_earnings_sessions(rows, adapter, "2026-05-27", 3)

        self.assertEqual(adapter.tickers, ["BBB"])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(merged[0]["next_earnings_session"], "after_market")

    def test_confirm_upcoming_retries_option_fetch_errors(self):
        class RecordingAdapter:
            def __init__(self):
                self.tickers = []

            def get_earnings_calendar(self, ticker, as_of, seed):
                self.tickers.append(ticker)
                return {
                    **seed,
                    "next_earnings_session": "after_market",
                    "has_option_expiring_within_7d": False,
                    "option_expiration_source": "yfinance",
                    "checked_at": as_of,
                }

        rows = [
            {
                "ticker": "AAA",
                "next_earnings_date": "2026-05-28",
                "next_earnings_session": "after_market",
                "has_option_expiring_within_7d": "",
                "option_expiration_source": "fetch_error",
                "checked_at": "2026-05-27",
            }
        ]
        adapter = RecordingAdapter()

        merged, confirmed = ec.confirm_upcoming_earnings_sessions(rows, adapter, "2026-05-27", 3)

        self.assertEqual(adapter.tickers, ["AAA"])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(merged[0]["has_option_expiring_within_7d"], False)

    def test_confirm_upcoming_honors_limit_and_leaves_remaining_unconfirmed(self):
        class RecordingAdapter:
            def get_earnings_calendar(self, ticker, as_of, seed):
                return {
                    **seed,
                    "next_earnings_session": "before_market",
                    "has_option_expiring_within_7d": False,
                    "checked_at": as_of,
                }

        rows = [
            {"ticker": "AAA", "next_earnings_date": "2026-05-28"},
            {"ticker": "BBB", "next_earnings_date": "2026-05-29"},
        ]

        merged, confirmed = ec.confirm_upcoming_earnings_sessions(
            rows,
            RecordingAdapter(),
            "2026-05-27",
            3,
            confirmation_limit=1,
        )

        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(ec.unconfirmed_upcoming_earnings(merged, "2026-05-27", 3)), 1)

    def test_update_checkpoint_preserves_partial_until_confirmations_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / ".earnings_calendar.csv.partial"
            rows = [{"ticker": "AAA", "next_earnings_date": "2026-05-28"}]

            remaining = ec.update_checkpoint_for_remaining_confirmations(rows, checkpoint, "2026-05-27", 3)

            self.assertEqual(remaining, 1)
            self.assertTrue(checkpoint.exists())

            completed_rows = [
                {
                    "ticker": "AAA",
                    "next_earnings_date": "2026-05-28",
                    "next_earnings_session": "before_market",
                    "has_option_expiring_within_7d": False,
                    "checked_at": "2026-05-27",
                }
            ]

            remaining = ec.update_checkpoint_for_remaining_confirmations(completed_rows, checkpoint, "2026-05-27", 3)

            self.assertEqual(remaining, 0)
            self.assertFalse(checkpoint.exists())

    def test_command_refresh_defaults_to_due_and_removes_checkpoint_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tickers_csv = root / "tickers.csv"
            output_dir = root / "buffett"
            cache_dir = root / "cache"
            output_dir.mkdir(parents=True)
            with tickers_csv.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["ticker", "company_name"])
                writer.writeheader()
                writer.writerow({"ticker": "AAA", "company_name": "AAA Inc."})
                writer.writerow({"ticker": "BBB", "company_name": "BBB Inc."})
            bp.write_csv(
                output_dir / "earnings_calendar.csv",
                [
                    {
                        "ticker": "AAA",
                        "company_name": "AAA Inc.",
                        "next_earnings_date": "2026-08-01",
                        "checked_at": "2026-05-26",
                    },
                    {"ticker": "BBB", "company_name": "BBB Inc.", "next_earnings_date": ""},
                ],
                ec.EARNINGS_CALENDAR_FIELDS,
            )

            original_adapter = ec.EarningsCalendarAdapter

            class TestAdapter(original_adapter):
                tickers = []

                def __init__(self, *args, **kwargs):
                    super().__init__(
                        cache_dir=cache_dir,
                        ticker_factory=lambda ticker: FakeEarningsTicker(ticker),
                        max_retries=0,
                    )

                def get_earnings_calendar(self, ticker, as_of, seed=None):
                    self.tickers.append(ticker)
                    return super().get_earnings_calendar(ticker, as_of, seed)

            ec.EarningsCalendarAdapter = TestAdapter
            try:
                code = ec.main(
                    [
                        "refresh",
                        "--as-of",
                        "2026-05-27",
                        "--tickers-csv",
                        str(tickers_csv),
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--checkpoint-interval",
                        "1",
                    ]
                )
            finally:
                ec.EarningsCalendarAdapter = original_adapter

            rows = bp.read_csv_rows(output_dir / "earnings_calendar.csv")
            checkpoint_exists = (output_dir / ".earnings_calendar.csv.partial").exists()

        self.assertEqual(code, 0)
        self.assertIn("BBB", TestAdapter.tickers)
        self.assertNotIn("AAA", TestAdapter.tickers)
        self.assertFalse(checkpoint_exists)
        self.assertEqual(len(rows), 2)


class BuffettDailySummaryCalendarTests(unittest.TestCase):
    def test_buffett_summary_can_list_all_calendar_earnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            signal_rows = [{"ticker": "GOOD", "company_name": "Good Co.", "next_earnings_date": "2026-08-01"}]
            calendar_rows = [
                {"ticker": "AAA", "company_name": "AAA Inc.", "next_earnings_date": "2026-05-29"},
                {"ticker": "BBB", "company_name": "BBB Inc.", "next_earnings_date": "2026-06-10"},
            ]

            bp.write_daily_summary(path, signal_rows, "2026-05-27", calendar_rows)

            text = path.read_text(encoding="utf-8")
            self.assertIn("Earnings in next 3 days: 1", text)
            self.assertIn("| AAA | AAA Inc. | 2026-05-29 | 未知 | Unknown |", text)
            self.assertNotIn("| GOOD | Good Co.", text)


if __name__ == "__main__":
    unittest.main()
