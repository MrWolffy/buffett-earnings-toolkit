import csv
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.tools import buffett_pipeline as bp


class BuyZoneEvaluationTests(unittest.TestCase):
    def test_in_buy_zone_is_actionable(self):
        result = bp.evaluate_buy_zone(90.0, 80.0, 100.0)
        self.assertEqual(result.status, "in_buy_zone")
        self.assertTrue(result.actionable)

    def test_below_buy_zone_is_actionable(self):
        result = bp.evaluate_buy_zone(75.0, 80.0, 100.0)
        self.assertEqual(result.status, "below_buy_zone")
        self.assertTrue(result.actionable)

    def test_near_buy_zone_uses_upper_bound_buffer(self):
        result = bp.evaluate_buy_zone(104.0, 80.0, 100.0)
        self.assertEqual(result.status, "near_buy_zone")
        self.assertTrue(result.actionable)

    def test_above_buy_zone_is_not_actionable(self):
        result = bp.evaluate_buy_zone(106.0, 80.0, 100.0)
        self.assertEqual(result.status, "above_buy_zone")
        self.assertFalse(result.actionable)

    def test_missing_buy_zone(self):
        result = bp.evaluate_buy_zone(90.0, None, 100.0)
        self.assertEqual(result.status, "missing_buy_zone")
        self.assertFalse(result.actionable)


class TickerUniverseTests(unittest.TestCase):
    def test_loads_and_dedupes_ticker_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tickers.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["ticker", "company_name", "sector", "industry", "roic"])
                writer.writeheader()
                writer.writerow({"ticker": "msft", "company_name": "Microsoft", "sector": "Technology", "industry": "Software", "roic": "0.2"})
                writer.writerow({"ticker": "MSFT", "company_name": "Microsoft Corp.", "sector": "Technology", "industry": "Software", "roic": "0.3"})
                writer.writerow({"ticker": "AAPL", "company_name": "Apple", "sector": "Technology", "industry": "Devices", "roic": "0.4"})

            rows = bp.load_ticker_universe(path)

        self.assertEqual([row["ticker"] for row in rows], ["AAPL", "MSFT"])
        self.assertEqual(rows[1]["company_name"], "Microsoft Corp.")
        self.assertEqual(rows[1]["source_roic"], "0.3")


class DateParsingTests(unittest.TestCase):
    def test_parse_date_accepts_localized_csv_dates(self):
        self.assertEqual(bp.parse_date("23/5/2026").isoformat(), "2026-05-23")

    def test_localized_last_analysis_date_does_not_force_reanalysis(self):
        record = {"last_analysis_date": "23/5/2026", "last_earnings_date": "30/4/2026"}
        snapshot = {
            "last_earnings_date": "",
            "last_earnings_has_actual": False,
            "latest_quarterly_statement_date": "2026-03-31",
        }

        self.assertFalse(bp.needs_reanalysis(record, snapshot))


class CsvBooleanTests(unittest.TestCase):
    def test_is_truthy_accepts_uppercase_csv_true(self):
        self.assertTrue(bp.is_truthy("TRUE"))


class OptionExpirationTests(unittest.TestCase):
    def test_option_expiration_info_flags_expirations_within_next_seven_days(self):
        info = bp.option_expiration_info(
            ["2026-05-26", "2026-05-29", "2026-06-05"],
            "2026-05-27",
        )

        self.assertTrue(info["has_option_expiring_within_7d"])
        self.assertEqual(info["next_option_expiration_date"], "2026-05-29")
        self.assertEqual(info["option_expirations_within_7d"], "2026-05-29")

    def test_option_expiration_info_returns_false_when_no_expiration_is_in_window(self):
        info = bp.option_expiration_info(["2026-06-10"], "2026-05-27")

        self.assertFalse(info["has_option_expiring_within_7d"])
        self.assertEqual(info["next_option_expiration_date"], "")


class BuffettOutputParsingTests(unittest.TestCase):
    def test_parses_json_fence_and_normalizes_reversed_buy_zone(self):
        text = """
```json
{
  "ticker": "AAPL",
  "is_good_company": true,
  "quality_score": 88,
  "moat_assessment": "strong/stable",
  "buy_zone": {
    "lower": 120,
    "upper": 100,
    "currency": "USD",
    "basis": "DCF range",
    "as_of": "2026-05-21"
  }
}
```
"""
        result = bp.parse_buffett_analysis_output(text, "AAPL", "2026-05-21")
        self.assertTrue(result["is_good_company"])
        self.assertEqual(result["buy_zone_lower"], 100.0)
        self.assertEqual(result["buy_zone_upper"], 120.0)
        self.assertEqual(result["buy_zone_basis"], "DCF range")

    def test_normalizes_zero_to_ten_quality_score_to_zero_to_one_hundred(self):
        text = """
{
  "ticker": "AAPL",
  "is_good_company": true,
  "quality_score": 8.7,
  "moat_assessment": "strong",
  "buy_zone": {"lower": 100, "upper": 120, "currency": "USD", "basis": "DCF", "as_of": "2026-05-21"}
}
"""
        result = bp.parse_buffett_analysis_output(text, "AAPL", "2026-05-21")

        self.assertEqual(result["quality_score"], 87.0)

    def test_non_json_output_raises(self):
        with self.assertRaises(ValueError):
            bp.parse_buffett_analysis_output("not json", "AAPL", "2026-05-21")

    def test_skill_prompt_keeps_fixed_instructions_before_company_data(self):
        snapshot = {"ticker": "AAPL", "company_name": "Apple Inc.", "currency": "USD"}

        prompt = bp.build_buffett_skill_prompt(snapshot, "2026-05-21")

        self.assertLess(prompt.index("Use the local buffett skill"), prompt.index("Ticker: AAPL"))
        self.assertNotIn("Return JSON only", prompt)

    def test_skill_batch_payload_uses_web_and_keeps_company_data_in_user_message(self):
        snapshot = {"ticker": "AAPL", "company_name": "Apple Inc.", "currency": "USD"}

        payload = bp.build_buffett_skill_response_payload(snapshot, "2026-05-21", "gpt-5.4", use_web=True)

        self.assertEqual(payload["tools"], [{"type": "web_search_preview"}])
        self.assertIn("Use the local buffett skill", payload["input"][0]["content"])
        self.assertIn("Ticker: AAPL", payload["input"][1]["content"])
        self.assertNotIn("Ticker: AAPL", payload["input"][0]["content"])

    def test_skill_batch_request_custom_ids_can_be_made_unique(self):
        snapshots = [
            ("00000_BRK_B", {"ticker": "BRK.B"}),
            ("00001_BRK_B", {"ticker": "BRK/B"}),
        ]

        requests = bp.build_skill_batch_requests(snapshots, "2026-05-21", "gpt-5.4", use_web=False)

        self.assertEqual([request["custom_id"] for request in requests], ["00000_BRK_B", "00001_BRK_B"])

    def test_structuring_payload_preserves_requested_model_and_json_schema(self):
        snapshot = {"ticker": "AAPL", "company_name": "Apple Inc.", "currency": "USD"}
        prompt = bp.build_buffett_structure_prompt("Apple has a durable consumer ecosystem.", snapshot, "2026-05-21")

        payload = bp.build_structured_response_payload(prompt, "test-structuring-model")

        self.assertEqual(payload["model"], "test-structuring-model")
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertIn("Convert a Buffett-style prose", payload["input"][0]["content"])


class ResumePolicyTests(unittest.TestCase):
    def test_missing_market_data_analysis_does_not_resume(self):
        analysis = {
            "last_analysis_date": "2026-05-21",
            "snapshot": {"as_of": "2026-05-21", "data_status": "missing_market_data"},
        }

        self.assertFalse(bp.analysis_matches_as_of(analysis, "2026-05-21"))


class EarningsFrameTests(unittest.TestCase):
    def test_extracts_future_and_historical_earnings_dates(self):
        frame = pd.DataFrame(
            {
                "Reported EPS": [1.2, None],
                "EPS Estimate": [1.1, 1.3],
            },
            index=pd.to_datetime(["2026-05-20", "2026-08-01"]),
        )

        info = bp.earnings_info_from_frame(frame, "2026-05-21")

        self.assertEqual(info["next_earnings_date"], "2026-08-01")
        self.assertEqual(info["next_earnings_source"], "yfinance")
        self.assertEqual(info["last_earnings_date"], "2026-05-20")
        self.assertTrue(info["last_earnings_has_actual"])

    def test_extracts_before_and_after_market_session_from_timezone_aware_index(self):
        before_frame = pd.DataFrame(
            {"Reported EPS": [None]},
            index=pd.DatetimeIndex(["2026-05-29 08:00:00"], tz="America/New_York"),
        )
        after_frame = pd.DataFrame(
            {"Reported EPS": [None]},
            index=pd.DatetimeIndex(["2026-05-29 16:00:00"], tz="America/New_York"),
        )

        before = bp.earnings_info_from_frame(before_frame, "2026-05-27")
        after = bp.earnings_info_from_frame(after_frame, "2026-05-27")

        self.assertEqual(before["next_earnings_session"], "before_market")
        self.assertEqual(after["next_earnings_session"], "after_market")
        self.assertIn("08:00:00", before["next_earnings_datetime"])


class FakeTicker:
    def __init__(self, ticker):
        self.ticker = ticker
        self.fast_info = {"currency": "USD", "shares": 100.0}
        self.quarterly_financials = pd.DataFrame(
            {"2026-03-31": {"Operating Income": 100.0}}
        )
        self.quarterly_balance_sheet = pd.DataFrame(
            {"2026-03-31": {"Total Debt": 10.0}}
        )
        self.quarterly_cashflow = pd.DataFrame(
            {"2026-03-31": {"Free Cash Flow": 80.0}}
        )

    def get_info(self):
        return {
            "longName": "Example Inc.",
            "sector": "Technology",
            "industry": "Software",
            "returnOnEquity": 0.2,
            "operatingMargins": 0.3,
            "debtToEquity": 20.0,
            "freeCashflow": 1000.0,
            "sharesOutstanding": 100.0,
        }

    def history(self, start=None, end=None):
        return pd.DataFrame(
            {"Close": [99.0, 101.0]},
            index=pd.to_datetime(["2026-05-20", "2026-05-21"]),
        )

    def get_earnings_dates(self, limit=16):
        return pd.DataFrame(
            {"Reported EPS": [2.0, None]},
            index=pd.to_datetime(["2026-05-20", "2026-08-01"]),
        )


class NoHistoryTicker(FakeTicker):
    def history(self, start=None, end=None):
        raise RuntimeError("market data unavailable")


class YFinanceAdapterTests(unittest.TestCase):
    def test_latest_close_skips_nan_close_rows(self):
        frame = pd.DataFrame(
            {"Close": [101.0, float("nan")]},
            index=pd.to_datetime(["2026-06-12", "2026-06-16"]),
        )

        close, close_date = bp.latest_close_from_history(frame, "2026-06-18")

        self.assertEqual(close, 101.0)
        self.assertEqual(close_date, "2026-06-12")

    def test_real_yfinance_cache_is_project_writable(self):
        calls = []
        fake_yf = types.SimpleNamespace(
            set_tz_cache_location=lambda cache_dir: calls.append(cache_dir),
            Ticker=lambda ticker: {"ticker": ticker},
        )

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"yfinance": fake_yf}):
            adapter = bp.YFinanceAdapter(cache_dir=Path(tmp), max_retries=0)
            ticker = adapter._ticker("TEST")
            self.assertTrue((Path(tmp) / "_yfinance").exists())

        self.assertEqual(ticker, {"ticker": "TEST"})
        self.assertEqual(calls, [str(Path(tmp) / "_yfinance")])

    def test_snapshot_uses_fake_ticker_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = bp.YFinanceAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: FakeTicker(ticker),
                max_retries=0,
            )
            snapshot = adapter.get_snapshot("TEST", "2026-05-21", {"source_roic": "0.25"})

        self.assertEqual(snapshot["ticker"], "TEST")
        self.assertEqual(snapshot["company_name"], "Example Inc.")
        self.assertEqual(snapshot["latest_close"], 101.0)
        self.assertEqual(snapshot["next_earnings_date"], "2026-08-01")
        self.assertEqual(snapshot["latest_quarterly_statement_date"], "2026-03-31")

    def test_snapshot_marks_missing_market_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = bp.YFinanceAdapter(
                cache_dir=Path(tmp),
                ticker_factory=lambda ticker: NoHistoryTicker(ticker),
                max_retries=0,
            )
            snapshot = adapter.get_snapshot("TEST", "2026-05-21", {"source_roic": "0.25"})

        self.assertEqual(snapshot["data_status"], "missing_market_data")
        self.assertIsNone(snapshot["latest_close"])
        self.assertIn("market data unavailable", snapshot["market_data_error"])

    def test_missing_market_data_cache_is_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_path = cache_dir / "TEST_2026-05-21.json"
            cache_path.write_text(
                json.dumps({"ticker": "TEST", "as_of": "2026-05-21", "data_status": "missing_market_data"}),
                encoding="utf-8",
            )
            adapter = bp.YFinanceAdapter(
                cache_dir=cache_dir,
                ticker_factory=lambda ticker: FakeTicker(ticker),
                max_retries=0,
            )
            snapshot = adapter.get_snapshot("TEST", "2026-05-21", {"source_roic": "0.25"})

        self.assertEqual(snapshot["data_status"], "ok")
        self.assertEqual(snapshot["latest_close"], 101.0)


class ReanalysisTests(unittest.TestCase):
    def test_new_earnings_after_last_analysis_triggers_reanalysis(self):
        record = {"last_analysis_date": "2026-05-19", "last_earnings_date": "2026-02-01"}
        snapshot = {"last_earnings_date": "2026-05-20", "last_earnings_has_actual": True}
        self.assertTrue(bp.needs_reanalysis(record, snapshot))

    def test_same_earnings_date_with_localized_format_does_not_trigger_reanalysis(self):
        record = {"last_analysis_date": "18/6/2026", "last_earnings_date": "9/11/2016"}
        snapshot = {"last_earnings_date": "2016-11-09", "last_earnings_has_actual": True}
        self.assertFalse(bp.needs_reanalysis(record, snapshot))

    def test_old_statement_does_not_trigger_reanalysis(self):
        record = {"last_analysis_date": "2026-05-21", "last_earnings_date": "2026-05-20"}
        snapshot = {"latest_quarterly_statement_date": "2026-03-31"}
        self.assertFalse(bp.needs_reanalysis(record, snapshot))

    def test_only_due_or_missing_earnings_need_calendar_check(self):
        self.assertTrue(bp.needs_earnings_check({"next_earnings_date": ""}, "2026-05-24"))
        self.assertTrue(bp.needs_earnings_check({"next_earnings_date": "2026-05-23"}, "2026-05-24"))
        self.assertFalse(bp.needs_earnings_check({"next_earnings_date": "2026-05-24"}, "2026-05-24"))
        self.assertFalse(bp.needs_earnings_check({"next_earnings_date": "2026-08-01"}, "2026-05-24"))


class MergeRecordTests(unittest.TestCase):
    def test_rejected_company_is_not_actionable_and_score_is_normalized(self):
        record = bp.merge_record(
            {"ticker": "AAA"},
            {"ticker": "AAA", "latest_close": 90, "latest_close_date": "2026-05-21"},
            {
                "ticker": "AAA",
                "is_good_company": False,
                "quality_score": 5.2,
                "buy_zone_lower": 80,
                "buy_zone_upper": 100,
            },
            "2026-05-21",
        )

        self.assertEqual(record["buy_zone_status"], "in_buy_zone")
        self.assertEqual(record["quality_score"], 52.0)
        self.assertFalse(record["actionable"])


class GoodCompanyMergeTests(unittest.TestCase):
    def test_good_company_fields_include_manual_calibration_columns(self):
        self.assertIn("manual_calibrated", bp.GOOD_COMPANIES_FIELDS)
        self.assertIn("manual_calibration_date", bp.GOOD_COMPANIES_FIELDS)

    def test_merge_preserves_existing_manual_calibration_when_reanalysis_omits_it(self):
        existing = [
            {
                "ticker": "AAA",
                "is_good_company": "True",
                "manual_calibrated": "true",
                "manual_calibration_date": "2026-05-24",
            }
        ]
        analyzed = [{"ticker": "AAA", "is_good_company": True, "quality_score": 90}]

        merged = bp.merge_good_company_rows(existing, analyzed, {"AAA"})

        self.assertEqual(merged[0]["manual_calibrated"], "true")
        self.assertEqual(merged[0]["manual_calibration_date"], "2026-05-24")


class DailySummaryTests(unittest.TestCase):
    def test_summary_lists_companies_reanalyzed_with_buffett(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            rows = [
                {
                    "ticker": "AAA",
                    "company_name": "AAA Inc.",
                    "is_good_company": True,
                    "quality_score": 88,
                    "reanalyzed_today": True,
                },
                {"ticker": "BBB", "company_name": "BBB Inc.", "reanalyzed_today": False},
            ]

            bp.write_daily_summary(path, rows, "2026-05-27")

            text = path.read_text(encoding="utf-8")
            self.assertIn("Companies reanalyzed with Buffett: 1", text)
            self.assertIn("## Reanalyzed With Buffett", text)
            self.assertIn("| AAA | AAA Inc. | True | 88 |", text)

    def test_summary_accepts_csv_string_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            rows = [
                {
                    "ticker": "AAA",
                    "latest_close": "90.125",
                    "buy_zone_status": "in_buy_zone",
                    "buy_zone_lower": "80",
                    "buy_zone_upper": "100",
                    "next_earnings_date": "2026-08-01",
                    "actionable": "True",
                    "is_good_company": "TRUE",
                }
            ]

            bp.write_daily_summary(path, rows, "2026-05-27")

            text = path.read_text(encoding="utf-8")
            self.assertIn("| AAA | 90.12 | in_buy_zone | 80-100 | 2026-08-01 |", text)

    def test_summary_excludes_actionable_company_rejected_after_reanalysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            rows = [
                {
                    "ticker": "AAA",
                    "company_name": "AAA Inc.",
                    "is_good_company": False,
                    "latest_close": 90,
                    "buy_zone_lower": 80,
                    "buy_zone_upper": 100,
                    "buy_zone_status": "in_buy_zone",
                    "actionable": True,
                }
            ]

            bp.write_daily_summary(path, rows, "2026-05-27")

            text = path.read_text(encoding="utf-8")
            self.assertIn("Actionable buy-zone signals: 0", text)
            self.assertNotIn("| AAA | 90", text)

    def test_summary_lists_earnings_within_next_three_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            rows = [
                {
                    "ticker": "AAA",
                    "company_name": "AAA Inc.",
                    "next_earnings_date": "2026-05-27",
                    "has_option_expiring_within_7d": True,
                    "next_option_expiration_date": "2026-05-29",
                },
                {"ticker": "BBB", "company_name": "BBB Inc.", "next_earnings_date": "29/5/2026"},
                {"ticker": "CCC", "company_name": "CCC Inc.", "next_earnings_date": "2026-06-01"},
            ]

            bp.write_daily_summary(path, rows, "2026-05-27")

            text = path.read_text(encoding="utf-8")
            self.assertIn("Earnings in next 3 days: 2", text)
            self.assertIn("## Earnings In Next 3 Days", text)
            self.assertIn("| AAA | AAA Inc. | 2026-05-27 | 未知 | Yes (2026-05-29) |", text)
            self.assertIn("| BBB | BBB Inc. | 29/5/2026 | 未知 | Unknown |", text)
            self.assertNotIn("| CCC | CCC Inc.", text)


class CliSmokeTests(unittest.TestCase):
    def test_build_and_analyze_small_watchlist_with_local_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tickers_csv = root / "tickers.csv"
            output_dir = root / "buffett"
            cache_dir = root / "cache"
            with tickers_csv.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["ticker", "company_name", "sector", "industry", "roic"])
                writer.writeheader()
                writer.writerow({"ticker": "TEST", "company_name": "Example", "sector": "Technology", "industry": "Software", "roic": "0.25"})

            build_code = bp.main(["build-watchlist", "--tickers-csv", str(tickers_csv), "--output-dir", str(output_dir)])
            self.assertEqual(build_code, 0)

            with patch.object(bp, "YFinanceAdapter") as adapter_cls:
                adapter_cls.return_value.get_snapshot.return_value = {
                    "ticker": "TEST",
                    "company_name": "Example Inc.",
                    "sector": "Technology",
                    "industry": "Software",
                    "source_roic": "0.25",
                    "currency": "USD",
                    "latest_close": 90.0,
                    "latest_close_date": "2026-05-21",
                    "next_earnings_date": "2026-08-01",
                    "next_earnings_source": "yfinance",
                    "last_earnings_date": "2026-05-20",
                    "data_status": "ok",
                    "info": {
                        "returnOnEquity": 0.2,
                        "operatingMargins": 0.25,
                        "debtToEquity": 10.0,
                        "freeCashflow": 1000.0,
                        "sharesOutstanding": 100.0,
                    },
                    "fast_info": {},
                }

                analyze_code = bp.main(
                    [
                        "analyze-watchlist",
                        "--as-of",
                        "2026-05-21",
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--analysis-mode",
                        "local",
                    ]
                )

            self.assertEqual(analyze_code, 0)
            good_rows = bp.read_csv_rows(output_dir / "good_companies.csv")
            self.assertEqual(len(good_rows), 1)
            self.assertEqual(good_rows[0]["ticker"], "TEST")
            self.assertTrue((output_dir / "analysis" / "TEST.json").exists())

    def test_daily_monitor_limit_preserves_unprocessed_good_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "buffett"
            cache_dir = Path(tmp) / "cache"
            bp.write_csv(
                output_dir / "good_companies.csv",
                [
                    {
                        "ticker": "AAA",
                        "company_name": "AAA Inc.",
                        "is_good_company": True,
                        "buy_zone_lower": 80,
                        "buy_zone_upper": 100,
                        "buy_zone_currency": "USD",
                        "last_analysis_date": "2026-05-21",
                    },
                    {
                        "ticker": "BBB",
                        "company_name": "BBB Inc.",
                        "is_good_company": True,
                        "buy_zone_lower": 40,
                        "buy_zone_upper": 50,
                        "buy_zone_currency": "USD",
                        "last_analysis_date": "2026-05-21",
                    },
                ],
                bp.GOOD_COMPANIES_FIELDS,
            )

            with patch.object(bp, "YFinanceAdapter") as adapter_cls:
                adapter_cls.return_value.get_price_snapshots.return_value = {
                    "AAA": {
                        "ticker": "AAA",
                        "latest_close": 90.0,
                        "latest_close_date": "2026-05-21",
                        "data_status": "ok",
                    }
                }
                adapter_cls.return_value.get_snapshot.return_value = {
                    "ticker": "AAA",
                    "company_name": "AAA Inc.",
                    "currency": "USD",
                    "latest_close": 90.0,
                    "latest_close_date": "2026-05-21",
                    "next_earnings_date": "2026-08-01",
                    "next_earnings_source": "yfinance",
                    "last_earnings_date": "2026-05-20",
                    "last_earnings_has_actual": True,
                    "latest_quarterly_statement_date": "2026-03-31",
                    "data_status": "ok",
                }
                code = bp.main(
                    [
                        "daily-monitor",
                        "--as-of",
                        "2026-05-24",
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--limit",
                        "1",
                        "--skip-reanalysis",
                    ]
                )

            self.assertEqual(code, 0)
            good_rows = bp.read_csv_rows(output_dir / "good_companies.csv")
            self.assertEqual([row["ticker"] for row in good_rows], ["AAA", "BBB"])
            self.assertIn("manual_calibrated", good_rows[0])

    def test_daily_monitor_merges_existing_signal_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "buffett"
            cache_dir = Path(tmp) / "cache"
            bp.write_csv(
                output_dir / "good_companies.csv",
                [
                    {
                        "ticker": "AAA",
                        "company_name": "AAA Inc.",
                        "is_good_company": True,
                        "buy_zone_lower": 80,
                        "buy_zone_upper": 100,
                        "buy_zone_currency": "USD",
                        "last_analysis_date": "2026-05-21",
                    },
                    {
                        "ticker": "BBB",
                        "company_name": "BBB Inc.",
                        "is_good_company": True,
                        "buy_zone_lower": 40,
                        "buy_zone_upper": 50,
                        "buy_zone_currency": "USD",
                        "last_analysis_date": "2026-05-21",
                    },
                ],
                bp.GOOD_COMPANIES_FIELDS,
            )
            bp.write_csv(
                output_dir / "daily" / "2026-05-24_signals.csv",
                [{"ticker": "AAA", "is_good_company": True, "latest_close": 90, "buy_zone_status": "in_buy_zone"}],
                bp.SIGNAL_FIELDS,
            )

            with patch.object(bp, "YFinanceAdapter") as adapter_cls:
                adapter_cls.return_value.get_price_snapshots.return_value = {
                    "BBB": {
                        "ticker": "BBB",
                        "latest_close": 45.0,
                        "latest_close_date": "2026-05-21",
                        "data_status": "ok",
                    }
                }
                adapter_cls.return_value.get_snapshot.return_value = {
                    "ticker": "BBB",
                    "company_name": "BBB Inc.",
                    "currency": "USD",
                    "latest_close": 45.0,
                    "latest_close_date": "2026-05-21",
                    "next_earnings_date": "2026-08-01",
                    "next_earnings_source": "yfinance",
                    "last_earnings_date": "2026-05-20",
                    "last_earnings_has_actual": True,
                    "latest_quarterly_statement_date": "2026-03-31",
                    "data_status": "ok",
                }
                code = bp.main(
                    [
                        "daily-monitor",
                        "--as-of",
                        "2026-05-24",
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--tickers",
                        "BBB",
                        "--skip-reanalysis",
                    ]
                )

            self.assertEqual(code, 0)
            signal_rows = bp.read_csv_rows(output_dir / "daily" / "2026-05-24_signals.csv")
            self.assertEqual([row["ticker"] for row in signal_rows], ["AAA", "BBB"])

    def test_daily_monitor_skips_full_snapshot_when_earnings_is_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "buffett"
            cache_dir = Path(tmp) / "cache"
            bp.write_csv(
                output_dir / "good_companies.csv",
                [
                    {
                        "ticker": "AAA",
                        "company_name": "AAA Inc.",
                        "is_good_company": "TRUE",
                        "buy_zone_lower": 80,
                        "buy_zone_upper": 100,
                        "buy_zone_currency": "USD",
                        "next_earnings_date": "2026-08-01",
                        "next_earnings_source": "yfinance",
                        "last_analysis_date": "2026-05-21",
                    }
                ],
                bp.GOOD_COMPANIES_FIELDS,
            )

            with patch.object(bp, "YFinanceAdapter") as adapter_cls:
                adapter = adapter_cls.return_value
                adapter.get_price_snapshots.return_value = {
                    "AAA": {
                        "ticker": "AAA",
                        "latest_close": 90.0,
                        "latest_close_date": "2026-05-21",
                        "data_status": "ok",
                    }
                }
                code = bp.main(
                    [
                        "daily-monitor",
                        "--as-of",
                        "2026-05-24",
                        "--output-dir",
                        str(output_dir),
                        "--cache-dir",
                        str(cache_dir),
                        "--checkpoint-interval",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            adapter.get_snapshot.assert_not_called()
            signal_rows = bp.read_csv_rows(output_dir / "daily" / "2026-05-24_signals.csv")
            self.assertEqual(signal_rows[0]["buy_zone_status"], "in_buy_zone")
            self.assertFalse((output_dir / "daily" / ".2026-05-24_signals.partial.csv").exists())


if __name__ == "__main__":
    unittest.main()
