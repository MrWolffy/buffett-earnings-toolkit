"""Offline regressions for a fresh toolkit's public monitoring universe."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.tools import buffett_v2_pipeline as p


NOW = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)
CUTOFF = NOW.isoformat()
SINCE = "2026-11-01T00:00:00+00:00"


class PublicCompanyPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "v2"
        self.legacy = self.base / "nonexistent_legacy"
        self.pool = self.base / "company_pool.csv"
        self.write_pool([
            {"ticker": " aaa ", "buy_zone_upper": "PRIVATE_OLD_PRICE"},
            {"ticker": "BBB", "research_authorized": "true"},
            {"ticker": "AAA", "quality_score": "99"},
            {"ticker": "", "quality_score": "MISSING"},
            {"ticker": "N/A"},
            {"ticker": "CON"},
            {"ticker": "BAD$A"},
        ])

    def write_pool(self, rows):
        fields = ["ticker", "company_name", "quality_score", "buy_zone_upper", "research_authorized"]
        with self.pool.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def prepare(self, **overrides):
        args = dict(events=[], checks={t: {"status": "ok", "checked_through": CUTOFF}
                                      for t in ("AAA", "BBB")},
                    since=SINCE, now=NOW, universe_csv=self.pool)
        args.update(overrides)
        return p.prepare(self.root, self.legacy, **args)

    def test_clean_bootstrap_deduplicates_supported_symbols_and_reports_skips(self):
        self.assertEqual(p.universe(self.root, self.legacy, universe_csv=self.pool), ["AAA", "BBB"])
        output = p.read(self.root / "inbox/universe.json")
        self.assertEqual(output["company_pool"]["import_fields"], ["ticker"])
        self.assertEqual([row["ticker"] for row in output["company_pool"]["skipped_rows"]],
                         ["", "N/A", "CON", "BAD$A"])
        self.assertTrue(all(row["line"] >= 2 and row["reason"]
                            for row in output["company_pool"]["skipped_rows"]))
        self.assertFalse(self.legacy.exists())

    def test_cli_defaults_to_bundled_pool_without_legacy_outputs(self):
        result = subprocess.run(
            [sys.executable, str(Path(p.__file__)), "--root", str(self.root),
             "universe", "--legacy-root", str(self.legacy)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", check=True, cwd=p.REPO)
        pool = p.read_universe_csv(p.DEFAULT_UNIVERSE_CSV)
        expected = pool["tickers"]
        self.assertGreater(len(expected), 0)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["ticker_count"], len(expected))
        self.assertEqual(summary["skipped_row_count"], len(pool["skipped_rows"]))
        self.assertEqual(p.read(self.root / "inbox/universe.json")["tickers"], expected)
        self.assertFalse(self.legacy.exists())

    def test_daily_requires_full_valid_pool_checks_before_dispatch(self):
        with self.assertRaisesRegex(ValueError, "cover entire scope"):
            self.prepare(checks={"AAA": {"status": "ok", "checked_through": CUTOFF}})
        self.assertFalse((self.root / "runs").exists())

    def test_daily_pool_does_not_authorize_research_without_released_events(self):
        result = self.prepare()
        run = Path(result["run_dir"])
        self.assertEqual(result["selected"], [])
        self.assertEqual(p.read(run / "plan.json")["monitor_tickers"], ["AAA", "BBB"])
        self.assertEqual(p.read(run / "scope.json")["company_pool"]["tickers"], ["AAA", "BBB"])

    def test_daily_dispatches_only_verified_event_from_pool(self):
        event = {"ticker": "AAA", "event_id": "issuer-2026-q3", "kind": "earnings_release",
                 "status": "released", "primary_source_verified": True,
                 "published_at": "2026-11-04T20:00:00Z", "report_period": "2026-Q3",
                 "source_url": "https://example.test/issuer/q3"}
        result = self.prepare(events=[event])
        self.assertEqual(result["selected"], ["AAA"])
        plan = p.read(Path(result["run_dir"]) / "plan.json")
        self.assertEqual(plan["monitor_tickers"], ["AAA", "BBB"])
        self.assertEqual(plan["selected"]["AAA"]["reason"], "new_earnings")

    def test_manual_dispatch_is_only_explicit_ticker_even_with_full_pool(self):
        result = p.prepare(self.root, self.legacy, "manual", tickers=["CCC"],
                           reason="Explicit synthetic migration", now=NOW, universe_csv=self.pool)
        plan = p.read(Path(result["run_dir"]) / "plan.json")
        self.assertEqual(result["selected"], ["CCC"])
        self.assertEqual(plan["monitor_tickers"], ["AAA", "BBB", "CCC"])
        self.assertEqual(plan["selected"]["CCC"]["reason"], "manual_explicit")
        self.assertIsNone(p.read(Path(result["run_dir"]) / "state_before.json"))

    def test_pool_import_never_copies_valuation_or_authorization_columns(self):
        result = self.prepare()
        for artifact in (self.root / "runs" / result["run_id"] / "scope.json",
                         self.root / "runs" / result["run_id"] / "plan.json"):
            text = artifact.read_text(encoding="utf-8")
            for forbidden in ("PRIVATE_OLD_PRICE", "buy_zone_upper", "quality_score", "research_authorized"):
                self.assertNotIn(forbidden, text)
        self.assertFalse(self.legacy.exists())

    def test_pool_does_not_guess_that_valid_na_symbol_is_a_placeholder(self):
        self.write_pool([{"ticker": "NA"}, {"ticker": "BBB"}])
        self.assertEqual(p.read_universe_csv(self.pool)["tickers"], ["BBB", "NA"])

    def test_pool_rejects_missing_header_and_no_supported_symbols(self):
        self.pool.write_text("symbol\nAAA\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ticker column"):
            p.read_universe_csv(self.pool)
        self.write_pool([{"ticker": "CON"}, {"ticker": ""}])
        with self.assertRaisesRegex(ValueError, "no supported tickers"):
            p.read_universe_csv(self.pool)


if __name__ == "__main__":
    unittest.main()
