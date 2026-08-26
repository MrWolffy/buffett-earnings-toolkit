"""Yfinance-only Buffett research pipeline CLI.

This module intentionally avoids Financial Datasets. It reads an existing ticker
universe CSV, gathers price/financial/earnings data with yfinance, stores
Buffett-style analysis as JSON, and writes CSV/Markdown monitoring outputs.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TICKERS_CSV = Path("data/company_pool.csv")
DEFAULT_OUTPUT_DIR = Path("outputs/buffett")
DEFAULT_CACHE_DIR = Path(".cache/buffett_yfinance")
DEFAULT_GPT_MODEL = "gpt-5.6-sol"
DEFAULT_STRUCTURING_MODEL = "gpt-5.6-luna"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_FILES_URL = "https://api.openai.com/v1/files"
OPENAI_BATCHES_URL = "https://api.openai.com/v1/batches"
NEAR_BUY_ZONE_PCT = 0.05
OPTION_EXPIRATION_LOOKAHEAD_DAYS = 7

BUFFETT_SKILL_SYSTEM_PROMPT = """Use the local buffett skill's investment thinking system as the analytical framework.

You must produce a prose investment memo, not JSON, not YAML, and not a table intended for machine parsing.
Use web search for current business context, recent filings/news, management changes, and industry facts when they are relevant.
Use only durable, investment-relevant facts and clearly distinguish facts from judgment.
Apply the Buffett-style checklist: circle of competence, moat durability, management integrity, owner earnings quality, balance-sheet safety, valuation discipline, key risks, monitoring indicators, and the final good-company judgment.
A company can be a good company even if today's price is too high.
If management integrity or debt safety is an automatic veto, say that plainly.
When valuation is possible, discuss a conservative buy-zone range and the assumptions behind it.
"""

BUFFETT_STRUCTURE_SYSTEM_PROMPT = """Convert a Buffett-style prose investment memo into the required monitoring JSON.

Use the memo as the primary source and the yfinance snapshot only to resolve identifiers, currency, price, and dates.
Return JSON only. Do not add commentary.
Use null for unknown numeric values. Do not invent precise numbers when the memo does not support them.
"""

BUFFETT_ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "ticker",
        "is_good_company",
        "quality_score",
        "moat_assessment",
        "veto_reason",
        "key_assumptions",
        "buy_zone",
    ],
    "properties": {
        "ticker": {"type": "string"},
        "is_good_company": {"type": "boolean"},
        "quality_score": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 100,
            "description": "Business quality score on a 0-100 scale. Never use a 0-10 scale.",
        },
        "moat_assessment": {"type": "string"},
        "veto_reason": {"type": "string"},
        "key_assumptions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "buy_zone": {
            "type": "object",
            "additionalProperties": False,
            "required": ["lower", "upper", "currency", "basis", "as_of"],
            "properties": {
                "lower": {"type": ["number", "null"]},
                "upper": {"type": ["number", "null"]},
                "currency": {"type": "string"},
                "basis": {"type": "string"},
                "as_of": {"type": "string"},
            },
        },
    },
}

BATCH_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled", "cancelling"}

UNIVERSE_FIELDS = [
    "ticker",
    "company_name",
    "sector",
    "industry",
    "source_roic",
]

GOOD_COMPANIES_FIELDS = [
    "ticker",
    "company_name",
    "sector",
    "industry",
    "is_good_company",
    "quality_score",
    "moat_assessment",
    "veto_reason",
    "buy_zone_lower",
    "buy_zone_upper",
    "buy_zone_currency",
    "buy_zone_basis",
    "buy_zone_as_of",
    "latest_close",
    "buy_zone_status",
    "next_earnings_date",
    "next_earnings_source",
    "last_earnings_date",
    "last_analysis_date",
    "needs_reanalysis",
    "manual_calibrated",
    "manual_calibration_date",
]

SIGNAL_FIELDS = GOOD_COMPANIES_FIELDS + [
    "latest_close_date",
    "distance_to_buy_zone_upper",
    "actionable",
    "reanalyzed_today",
]

MANUAL_CALIBRATION_FIELDS = ["manual_calibrated", "manual_calibration_date"]


@dataclass
class BuyZone:
    lower: float | None
    upper: float | None
    currency: str | None = None
    basis: str | None = None
    as_of: str | None = None

    def normalized(self) -> "BuyZone":
        if self.lower is None or self.upper is None:
            return self
        if self.lower <= self.upper:
            return self
        return BuyZone(
            lower=self.upper,
            upper=self.lower,
            currency=self.currency,
            basis=self.basis,
            as_of=self.as_of,
        )


@dataclass
class BuyZoneEvaluation:
    status: str
    distance_to_buy_zone_upper: float | None
    actionable: bool


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            pass
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def earnings_session_from_datetime(value: Any) -> str:
    event_datetime = parse_datetime(value)
    if event_datetime is None or event_datetime.tzinfo is None:
        return "unknown"
    eastern = event_datetime.astimezone(ZoneInfo("America/New_York"))
    minutes = eastern.hour * 60 + eastern.minute
    if minutes < 9 * 60 + 30:
        return "before_market"
    if minutes >= 16 * 60:
        return "after_market"
    return "during_market"


def clean_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def normalize_quality_score(value: Any) -> float | None:
    score = clean_float(value)
    if score is None:
        return None
    if 0 < score <= 10:
        score *= 10
    return round(min(max(score, 0), 100), 2)


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    try:
        import pandas as pd

        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except Exception:
        pass
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def evaluate_buy_zone(
    latest_close: float | None,
    lower: float | None,
    upper: float | None,
    near_pct: float = NEAR_BUY_ZONE_PCT,
) -> BuyZoneEvaluation:
    if latest_close is None:
        return BuyZoneEvaluation("no_price", None, False)
    zone = BuyZone(lower, upper).normalized()
    if zone.lower is None or zone.upper is None:
        return BuyZoneEvaluation("missing_buy_zone", None, False)
    if zone.lower <= 0 or zone.upper <= 0:
        return BuyZoneEvaluation("invalid_buy_zone", None, False)

    distance = (latest_close - zone.upper) / zone.upper
    if latest_close < zone.lower:
        return BuyZoneEvaluation("below_buy_zone", distance, True)
    if latest_close <= zone.upper:
        return BuyZoneEvaluation("in_buy_zone", distance, True)
    if latest_close <= zone.upper * (1 + near_pct):
        return BuyZoneEvaluation("near_buy_zone", distance, True)
    return BuyZoneEvaluation("above_buy_zone", distance, False)


def load_ticker_universe(path: Path) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "ticker" not in [field.lower() for field in reader.fieldnames]:
            raise ValueError(f"{path} must contain a ticker column")

        field_map = {field.lower(): field for field in reader.fieldnames}
        for row in reader:
            raw_ticker = row.get(field_map["ticker"], "").strip()
            if not raw_ticker:
                continue
            ticker = raw_ticker.upper()
            rows[ticker] = {
                "ticker": ticker,
                "company_name": row.get(field_map.get("company_name", ""), "").strip(),
                "sector": row.get(field_map.get("sector", ""), "").strip(),
                "industry": row.get(field_map.get("industry", ""), "").strip(),
                "source_roic": row.get(field_map.get("roic", ""), "").strip(),
            }
    return [rows[ticker] for ticker in sorted(rows)]


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    last_error: OSError | None = None
    for attempt in range(5):
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.{attempt}.tmp")
        try:
            with temp_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in fieldnames})
            temp_path.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.5 * (attempt + 1))
    if last_error:
        raise last_error


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def normalize_buy_zone_fields(raw: dict[str, Any]) -> BuyZone:
    zone_data = raw.get("buy_zone")
    if isinstance(zone_data, dict):
        lower = clean_float(zone_data.get("lower"))
        upper = clean_float(zone_data.get("upper"))
        currency = zone_data.get("currency")
        basis = zone_data.get("basis")
        as_of = zone_data.get("as_of")
    else:
        lower = clean_float(raw.get("buy_zone_lower"))
        upper = clean_float(raw.get("buy_zone_upper"))
        currency = raw.get("buy_zone_currency")
        basis = raw.get("buy_zone_basis")
        as_of = raw.get("buy_zone_as_of")
    return BuyZone(lower, upper, currency, basis, as_of).normalized()


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        return json.loads(fence.group(1))

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return json.loads(text[first : last + 1])
    raise ValueError("No JSON object found in Buffett analysis output")


def parse_buffett_analysis_output(text: str, ticker: str, as_of: str) -> dict[str, Any]:
    data = extract_json_object(text)
    zone = normalize_buy_zone_fields(data)
    return {
        "ticker": str(data.get("ticker") or ticker).upper(),
        "is_good_company": bool(data.get("is_good_company", False)),
        "quality_score": normalize_quality_score(data.get("quality_score")),
        "moat_assessment": data.get("moat_assessment") or data.get("moat") or "",
        "veto_reason": data.get("veto_reason") or "",
        "key_assumptions": data.get("key_assumptions") or [],
        "buy_zone_lower": zone.lower,
        "buy_zone_upper": zone.upper,
        "buy_zone_currency": zone.currency or data.get("currency") or "USD",
        "buy_zone_basis": zone.basis or "",
        "buy_zone_as_of": zone.as_of or as_of,
        "raw_analysis": data,
    }


def dataframe_to_records(frame: Any, max_rows: int | None = None) -> list[dict[str, Any]]:
    try:
        import pandas as pd

        if frame is None or getattr(frame, "empty", True):
            return []
        data = frame.copy()
        if max_rows is not None:
            data = data.head(max_rows)
        data = data.reset_index()
        return json.loads(data.to_json(orient="records", date_format="iso"))
    except Exception:
        return []


def dataframe_columns_as_dates(frame: Any) -> list[str]:
    if frame is None or getattr(frame, "empty", True):
        return []
    dates: list[str] = []
    for column in getattr(frame, "columns", []):
        parsed = parse_date(column)
        if parsed:
            dates.append(parsed.isoformat())
    return sorted(set(dates), reverse=True)


def latest_close_from_history(frame: Any, as_of: str) -> tuple[float | None, str | None]:
    if frame is None or getattr(frame, "empty", True):
        return None, None
    target = parse_date(as_of)
    rows = frame.copy()
    try:
        rows = rows.sort_index()
        if target:
            rows = rows[[parse_date(index) is not None and parse_date(index) <= target for index in rows.index]]
        if rows.empty or "Close" not in rows:
            return None, None
        rows = rows[[clean_float(value) is not None for value in rows["Close"]]]
        if rows.empty:
            return None, None
        close = clean_float(rows["Close"].iloc[-1])
        close_date = parse_date(rows.index[-1])
        return close, close_date.isoformat() if close_date else None
    except Exception:
        return None, None


def earnings_info_from_frame(frame: Any, as_of: str) -> dict[str, Any]:
    records = dataframe_to_records(frame)
    target = parse_date(as_of) or date.today()
    future: list[tuple[date, datetime | None, dict[str, Any]]] = []
    past: list[tuple[date, datetime | None, dict[str, Any]]] = []

    for index, record in enumerate(records):
        raw_event = record.get("Earnings Date") or record.get("index") or record.get("Date")
        try:
            raw_event = frame.index[index]
        except Exception:
            pass
        event_datetime = parse_datetime(raw_event)
        event_date = event_datetime.date() if event_datetime else parse_date(raw_event)
        if not event_date:
            continue
        if event_date >= target:
            future.append((event_date, event_datetime, record))
        else:
            past.append((event_date, event_datetime, record))

    next_event = min(future, key=lambda item: item[0], default=None)
    next_date = next_event[0] if next_event else None
    next_datetime = next_event[1] if next_event else None
    latest_past = max(past, key=lambda item: item[0], default=None)
    latest_record = latest_past[2] if latest_past else {}
    eps_actual = (
        latest_record.get("Reported EPS")
        if "Reported EPS" in latest_record
        else latest_record.get("EPS Actual", latest_record.get("epsActual"))
    )

    return {
        "next_earnings_date": next_date.isoformat() if next_date else "",
        "next_earnings_datetime": next_datetime.isoformat() if next_datetime else "",
        "next_earnings_session": earnings_session_from_datetime(next_datetime),
        "next_earnings_source": "yfinance" if next_date else "missing",
        "last_earnings_date": latest_past[0].isoformat() if latest_past else "",
        "last_earnings_datetime": latest_past[1].isoformat() if latest_past and latest_past[1] else "",
        "last_earnings_has_actual": clean_float(eps_actual) is not None,
        "earnings_dates": records,
    }


class YFinanceAdapter:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        ticker_factory: Callable[[str], Any] | None = None,
        max_retries: int = 2,
        retry_sleep: float = 1.0,
        force_refresh: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.yfinance_cache_dir = cache_dir / "_yfinance"
        self.ticker_factory = ticker_factory
        self.max_retries = max_retries
        self.retry_sleep = retry_sleep
        self.force_refresh = force_refresh

    def _cache_path(self, ticker: str, as_of: str) -> Path:
        safe = ticker.replace("/", "_")
        return self.cache_dir / f"{safe}_{as_of}.json"

    def _price_cache_path(self, ticker: str, as_of: str) -> Path:
        safe = ticker.replace("/", "_")
        return self.cache_dir / "prices" / f"{safe}_{as_of}.json"

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

    def get_snapshot(self, ticker: str, as_of: str, seed: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._cache_path(ticker, as_of)
        if path.exists() and not self.force_refresh:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("data_status") not in {"fetch_error", "missing_market_data"}:
                return cached

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                snapshot = self._fetch_snapshot(ticker, as_of, seed or {})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(snapshot, indent=2, default=json_default), encoding="utf-8")
                return snapshot
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_sleep * (attempt + 1))

        return {
            "ticker": ticker,
            "as_of": as_of,
            "data_status": "fetch_error",
            "error": last_error,
            **(seed or {}),
        }

    def get_price_snapshots(self, rows: list[dict[str, Any]], as_of: str) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for row in rows:
            ticker = str(row.get("ticker", "")).upper()
            if not ticker:
                continue
            cached = self._read_price_snapshot(ticker, as_of)
            if cached:
                snapshots[ticker] = cached
            else:
                pending.append(row)

        if not pending:
            return snapshots

        if self.ticker_factory:
            for row in pending:
                ticker = str(row.get("ticker", "")).upper()
                snapshots[ticker] = self._fetch_price_snapshot_with_ticker_factory(ticker, as_of, row)
            return snapshots

        try:
            snapshots.update(self._fetch_batch_price_snapshots(pending, as_of))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            for row in pending:
                ticker = str(row.get("ticker", "")).upper()
                snapshot = self._price_snapshot_from_seed(ticker, as_of, row, None, None, "missing_market_data", error)
                self._write_price_snapshot(snapshot)
                snapshots[ticker] = snapshot

        return snapshots

    def _read_price_snapshot(self, ticker: str, as_of: str) -> dict[str, Any] | None:
        if self.force_refresh:
            return None
        price_path = self._price_cache_path(ticker, as_of)
        if price_path.exists():
            return json.loads(price_path.read_text(encoding="utf-8"))
        full_path = self._cache_path(ticker, as_of)
        if full_path.exists():
            cached = json.loads(full_path.read_text(encoding="utf-8"))
            if cached.get("latest_close") not in (None, ""):
                return {
                    "ticker": ticker,
                    "as_of": as_of,
                    "data_status": cached.get("data_status") or "ok",
                    "market_data_error": cached.get("market_data_error") or "",
                    "latest_close": cached.get("latest_close"),
                    "latest_close_date": cached.get("latest_close_date") or "",
                }
        return None

    def _write_price_snapshot(self, snapshot: dict[str, Any]) -> None:
        path = self._price_cache_path(str(snapshot["ticker"]), str(snapshot["as_of"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2, default=json_default), encoding="utf-8")

    def _price_snapshot_from_seed(
        self,
        ticker: str,
        as_of: str,
        seed: dict[str, Any],
        latest_close: float | None,
        latest_close_date: str | None,
        data_status: str,
        market_data_error: str = "",
    ) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "as_of": as_of,
            "company_name": seed.get("company_name") or "",
            "sector": seed.get("sector") or "",
            "industry": seed.get("industry") or "",
            "currency": seed.get("buy_zone_currency") or "USD",
            "data_status": data_status,
            "market_data_error": market_data_error,
            "latest_close": latest_close,
            "latest_close_date": latest_close_date or "",
        }

    def _fetch_price_snapshot_with_ticker_factory(self, ticker: str, as_of: str, seed: dict[str, Any]) -> dict[str, Any]:
        stock = self._ticker(ticker)
        end_date = (parse_date(as_of) or date.today()) + timedelta(days=1)
        start_date = end_date - timedelta(days=10)
        try:
            history = stock.history(start=start_date.isoformat(), end=end_date.isoformat())
            latest_close, latest_close_date = latest_close_from_history(history, as_of)
            status = "ok" if latest_close is not None else "missing_market_data"
            snapshot = self._price_snapshot_from_seed(ticker, as_of, seed, latest_close, latest_close_date, status)
        except Exception as exc:
            snapshot = self._price_snapshot_from_seed(
                ticker,
                as_of,
                seed,
                None,
                None,
                "missing_market_data",
                f"{type(exc).__name__}: {exc}",
            )
        self._write_price_snapshot(snapshot)
        return snapshot

    def _fetch_batch_price_snapshots(self, rows: list[dict[str, Any]], as_of: str) -> dict[str, dict[str, Any]]:
        import yfinance as yf

        self._configure_yfinance_cache(yf)
        tickers = [str(row.get("ticker", "")).upper() for row in rows if str(row.get("ticker", "")).strip()]
        end_date = (parse_date(as_of) or date.today()) + timedelta(days=1)
        start_date = end_date - timedelta(days=10)
        frame = yf.download(
            tickers=tickers,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
        )
        snapshots: dict[str, dict[str, Any]] = {}
        by_ticker = {str(row.get("ticker", "")).upper(): row for row in rows if str(row.get("ticker", "")).strip()}
        for ticker, seed in by_ticker.items():
            history = self._history_for_downloaded_ticker(frame, ticker, len(tickers) == 1)
            latest_close, latest_close_date = latest_close_from_history(history, as_of)
            status = "ok" if latest_close is not None else "missing_market_data"
            snapshot = self._price_snapshot_from_seed(ticker, as_of, seed, latest_close, latest_close_date, status)
            self._write_price_snapshot(snapshot)
            snapshots[ticker] = snapshot
        return snapshots

    @staticmethod
    def _history_for_downloaded_ticker(frame: Any, ticker: str, single_ticker: bool) -> Any:
        if frame is None or getattr(frame, "empty", True):
            return None
        if single_ticker:
            return frame
        try:
            if ticker in frame.columns.get_level_values(0):
                return frame[ticker]
        except Exception:
            pass
        return None

    def _fetch_snapshot(self, ticker: str, as_of: str, seed: dict[str, Any]) -> dict[str, Any]:
        stock = self._ticker(ticker)
        info = self._safe_dict(lambda: stock.get_info())
        if not info:
            info = self._safe_dict(lambda: stock.info)
        fast_info = self._safe_dict(lambda: dict(stock.fast_info))

        end_date = (parse_date(as_of) or date.today()) + timedelta(days=1)
        start_date = end_date - timedelta(days=45)
        history_error = ""
        try:
            history = stock.history(start=start_date.isoformat(), end=end_date.isoformat())
        except Exception as exc:
            history = None
            history_error = f"{type(exc).__name__}: {exc}"
        latest_close, latest_close_date = latest_close_from_history(history, as_of)
        data_status = "ok" if latest_close is not None else "missing_market_data"

        earnings_dates = self._safe_value(lambda: stock.get_earnings_dates(limit=16))
        earnings = earnings_info_from_frame(earnings_dates, as_of)

        quarterly_financials = self._safe_value(lambda: stock.quarterly_financials)
        quarterly_balance = self._safe_value(lambda: stock.quarterly_balance_sheet)
        quarterly_cashflow = self._safe_value(lambda: stock.quarterly_cashflow)
        statement_dates = sorted(
            set(
                dataframe_columns_as_dates(quarterly_financials)
                + dataframe_columns_as_dates(quarterly_balance)
                + dataframe_columns_as_dates(quarterly_cashflow)
            ),
            reverse=True,
        )

        currency = (
            fast_info.get("currency")
            or info.get("currency")
            or info.get("financialCurrency")
            or "USD"
        )
        return {
            "ticker": ticker,
            "as_of": as_of,
            "company_name": seed.get("company_name") or info.get("longName") or info.get("shortName") or "",
            "sector": seed.get("sector") or info.get("sector") or "",
            "industry": seed.get("industry") or info.get("industry") or "",
            "source_roic": seed.get("source_roic") or "",
            "currency": currency,
            "data_status": data_status,
            "market_data_error": history_error,
            "latest_close": latest_close,
            "latest_close_date": latest_close_date,
            "fast_info": fast_info,
            "info": {
                key: info.get(key)
                for key in [
                    "longName",
                    "shortName",
                    "sector",
                    "industry",
                    "returnOnEquity",
                    "returnOnAssets",
                    "operatingMargins",
                    "profitMargins",
                    "debtToEquity",
                    "freeCashflow",
                    "totalCash",
                    "totalDebt",
                    "sharesOutstanding",
                    "marketCap",
                    "currentPrice",
                    "currency",
                    "financialCurrency",
                ]
            },
            "next_earnings_date": earnings["next_earnings_date"],
            "next_earnings_datetime": earnings["next_earnings_datetime"],
            "next_earnings_session": earnings["next_earnings_session"],
            "next_earnings_source": earnings["next_earnings_source"],
            "last_earnings_date": earnings["last_earnings_date"],
            "last_earnings_datetime": earnings["last_earnings_datetime"],
            "last_earnings_has_actual": earnings["last_earnings_has_actual"],
            "earnings_dates": earnings["earnings_dates"],
            "quarterly_statement_dates": statement_dates,
            "latest_quarterly_statement_date": statement_dates[0] if statement_dates else "",
        }

    @staticmethod
    def _safe_value(func: Callable[[], Any]) -> Any:
        try:
            return func()
        except Exception:
            return None

    @staticmethod
    def _safe_dict(func: Callable[[], Any]) -> dict[str, Any]:
        try:
            value = func()
            return dict(value or {})
        except Exception:
            return {}


def build_buffett_prompt(snapshot: dict[str, Any], as_of: str) -> str:
    return build_buffett_skill_prompt(snapshot, as_of)


def build_buffett_skill_prompt(snapshot: dict[str, Any], as_of: str) -> str:
    return f"""{BUFFETT_SKILL_SYSTEM_PROMPT}

{build_buffett_company_prompt(snapshot, as_of)}
"""


def build_buffett_company_prompt(snapshot: dict[str, Any], as_of: str) -> str:
    return f"""Company-specific data follows. Analyze this company as of {as_of}.

Ticker: {snapshot.get("ticker", "")}
Company name: {snapshot.get("company_name", "")}
Sector: {snapshot.get("sector", "")}
Industry: {snapshot.get("industry", "")}
Currency: {snapshot.get("currency") or "USD"}

Yfinance data snapshot:
```json
{json.dumps(snapshot, indent=2, default=json_default)}
```
"""


def build_buffett_structure_prompt(raw_memo: str, snapshot: dict[str, Any], as_of: str) -> str:
    return f"""Company-specific data follows. Structure this Buffett memo as of {as_of}.

Ticker: {snapshot.get("ticker", "")}
Company name: {snapshot.get("company_name", "")}
Currency: {snapshot.get("currency") or "USD"}

Buffett prose memo:
```text
{raw_memo}
```

Yfinance data snapshot:
```json
{json.dumps(snapshot, indent=2, default=json_default)}
```
"""


def build_direct_structured_buffett_prompt(snapshot: dict[str, Any], as_of: str) -> str:
    return f"""Analyze this company with a Buffett-style quality and valuation lens, then return the required JSON shape.

Rules:
- Use only the supplied yfinance-derived data.
- A company can be a good company even when today's price is too high.
- quality_score must use a 0-100 scale. Never use a 0-10 scale.
- If management integrity or debt safety is an automatic veto, set is_good_company=false and explain veto_reason.
- The buy zone must be a range with lower and upper bounds when valuation is possible.

Company-specific data follows. Analyze this company as of {as_of}.

Ticker: {snapshot.get("ticker", "")}
Company name: {snapshot.get("company_name", "")}
Currency: {snapshot.get("currency") or "USD"}

Yfinance data snapshot:
```json
{json.dumps(snapshot, indent=2, default=json_default)}
```
"""


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def openai_api_key() -> str | None:
    load_dotenv_if_available()
    import os

    return os.environ.get("OPENAI_API_KEY")


def response_text_from_openai_payload(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    chunks: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def openai_auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def openai_json_headers(api_key: str) -> dict[str, str]:
    return {**openai_auth_headers(api_key), "Content-Type": "application/json"}


def build_structured_response_payload(
    prompt: str,
    model: str,
    schema_name: str = "buffett_company_analysis",
    system_prompt: str = BUFFETT_STRUCTURE_SYSTEM_PROMPT,
) -> dict[str, Any]:
    return {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": BUFFETT_ANALYSIS_SCHEMA,
            }
        },
    }


def build_buffett_skill_response_payload(
    snapshot: dict[str, Any],
    as_of: str,
    model: str,
    use_web: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": BUFFETT_SKILL_SYSTEM_PROMPT},
            {"role": "user", "content": build_buffett_company_prompt(snapshot, as_of)},
        ],
    }
    if use_web:
        payload["tools"] = [{"type": "web_search_preview"}]
    return payload


def run_buffett_gpt(snapshot: dict[str, Any], as_of: str, model: str = DEFAULT_GPT_MODEL) -> dict[str, Any]:
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; add it to the environment or .env")

    import httpx

    prompt = build_direct_structured_buffett_prompt(snapshot, as_of)
    payload = build_structured_response_payload(
        prompt,
        model,
        system_prompt=(
            "You are a disciplined Buffett-style investment analyst. "
            "Use only the supplied yfinance-derived data. "
            "quality_score must use a 0-100 scale, never a 0-10 scale. Return JSON only."
        ),
    )
    try:
        response = httpx.post(
            OPENAI_RESPONSES_URL,
            headers=openai_json_headers(api_key),
            json=payload,
            timeout=120,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI request failed before response: {type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        body = response.text[:2000]
        raise RuntimeError(f"OpenAI API returned HTTP {response.status_code}: {body}")

    data = response.json()
    text = response_text_from_openai_payload(data)
    if not text:
        raise RuntimeError(f"OpenAI response did not contain output text: {json.dumps(data)[:2000]}")
    parsed = parse_buffett_analysis_output(text, str(snapshot.get("ticker", "")), as_of)
    parsed["raw_openai_response_id"] = data.get("id")
    parsed["gpt_model"] = model
    return parsed


def structure_buffett_memo(
    raw_memo: str,
    snapshot: dict[str, Any],
    as_of: str,
    model: str = DEFAULT_STRUCTURING_MODEL,
) -> dict[str, Any]:
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; add it to the environment or .env")

    import httpx

    payload = build_structured_response_payload(
        build_buffett_structure_prompt(raw_memo, snapshot, as_of),
        model,
    )
    try:
        response = httpx.post(
            OPENAI_RESPONSES_URL,
            headers=openai_json_headers(api_key),
            json=payload,
            timeout=120,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI structuring request failed before response: {type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        body = response.text[:2000]
        raise RuntimeError(f"OpenAI structuring API returned HTTP {response.status_code}: {body}")

    data = response.json()
    text = response_text_from_openai_payload(data)
    if not text:
        raise RuntimeError(f"OpenAI structuring response did not contain output text: {json.dumps(data)[:2000]}")
    parsed = parse_buffett_analysis_output(text, str(snapshot.get("ticker", "")), as_of)
    parsed["raw_buffett_memo"] = raw_memo
    parsed["raw_openai_response_id"] = data.get("id")
    parsed["structuring_model"] = model
    return parsed


async def run_buffett_skill_async(snapshot: dict[str, Any], as_of: str) -> str:
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except Exception as exc:
        raise RuntimeError(f"claude_agent_sdk unavailable: {exc}") from exc

    chunks: list[str] = []
    stderr_lines: list[str] = []
    options = ClaudeAgentOptions(
        cwd=str(Path(__file__).resolve().parents[2]),
        allowed_tools=["Skill", "Read", "WebFetch", "WebSearch"],
        max_turns=25,
        stderr=stderr_lines.append,
    )
    try:
        async for message in query(prompt=build_buffett_prompt(snapshot, as_of), options=options):
            content = getattr(message, "content", None)
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        chunks.append(text)
    except Exception as exc:
        partial = "\n".join(chunks + stderr_lines).strip()
        detail = f"{type(exc).__name__}: {exc}"
        if partial:
            detail = f"{detail}; partial_output: {partial[:1500]}"
        raise RuntimeError(detail) from exc
    return "\n".join(chunks)


def run_buffett_skill(
    snapshot: dict[str, Any],
    as_of: str,
    structuring_model: str = DEFAULT_STRUCTURING_MODEL,
) -> dict[str, Any]:
    text = asyncio.run(run_buffett_skill_async(snapshot, as_of))
    return structure_buffett_memo(text, snapshot, as_of, model=structuring_model)


def batch_custom_id(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ticker.upper())


def write_batch_jsonl(path: Path, requests: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for request in requests:
            file.write(json.dumps(request, default=json_default) + "\n")


def upload_batch_input(api_key: str, path: Path) -> str:
    import httpx

    with path.open("rb") as file:
        response = httpx.post(
            OPENAI_FILES_URL,
            headers=openai_auth_headers(api_key),
            data={"purpose": "batch"},
            files={"file": (path.name, file, "application/jsonl")},
            timeout=120,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI file upload returned HTTP {response.status_code}: {response.text[:2000]}")
    file_id = response.json().get("id")
    if not file_id:
        raise RuntimeError(f"OpenAI file upload did not return an id: {response.text[:2000]}")
    return str(file_id)


def create_openai_batch(api_key: str, input_file_id: str, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    import httpx

    payload = {
        "input_file_id": input_file_id,
        "endpoint": "/v1/responses",
        "completion_window": "24h",
    }
    if metadata:
        payload["metadata"] = metadata
    response = httpx.post(
        OPENAI_BATCHES_URL,
        headers=openai_json_headers(api_key),
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI batch create returned HTTP {response.status_code}: {response.text[:2000]}")
    return response.json()


def retrieve_openai_batch(api_key: str, batch_id: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(
        f"{OPENAI_BATCHES_URL}/{batch_id}",
        headers=openai_auth_headers(api_key),
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI batch retrieve returned HTTP {response.status_code}: {response.text[:2000]}")
    return response.json()


def wait_for_openai_batch(
    api_key: str,
    batch_id: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        batch = retrieve_openai_batch(api_key, batch_id)
        status = str(batch.get("status", ""))
        if status in BATCH_TERMINAL_STATUSES:
            return batch
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError(f"OpenAI batch {batch_id} did not finish within {timeout_seconds} seconds")
        print(f"Batch {batch_id} status={status}; waiting {poll_seconds}s")
        time.sleep(poll_seconds)


def download_openai_file_text(api_key: str, file_id: str) -> str:
    import httpx

    response = httpx.get(
        f"{OPENAI_FILES_URL}/{file_id}/content",
        headers=openai_auth_headers(api_key),
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI file download returned HTTP {response.status_code}: {response.text[:2000]}")
    return response.text


def parse_batch_output(text: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        custom_id = str(item.get("custom_id", ""))
        response = item.get("response") or {}
        body = response.get("body") if isinstance(response, dict) else None
        error = item.get("error")
        if error:
            results[custom_id] = {"error": error}
        elif isinstance(body, dict):
            results[custom_id] = body
        else:
            results[custom_id] = {"error": f"missing response body: {json.dumps(item)[:1000]}"}
    return results


def run_responses_batch(
    requests: list[dict[str, Any]],
    output_dir: Path,
    label: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, dict[str, Any]]:
    if not requests:
        return {}
    api_key = openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; add it to the environment or .env")

    batch_dir = output_dir / "batches"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    input_path = batch_dir / f"{timestamp}_{label}_input.jsonl"
    output_path = batch_dir / f"{timestamp}_{label}_output.jsonl"
    write_batch_jsonl(input_path, requests)
    input_file_id = upload_batch_input(api_key, input_path)
    batch = create_openai_batch(api_key, input_file_id, metadata={"label": label})
    batch_id = str(batch.get("id", ""))
    if not batch_id:
        raise RuntimeError(f"OpenAI batch create did not return an id: {json.dumps(batch)[:2000]}")
    print(f"Submitted OpenAI batch {batch_id} for {len(requests)} {label} requests")
    batch = wait_for_openai_batch(api_key, batch_id, poll_seconds, timeout_seconds)
    if batch.get("status") != "completed":
        raise RuntimeError(f"OpenAI batch {batch_id} ended with status={batch.get('status')}: {json.dumps(batch)[:2000]}")
    output_file_id = batch.get("output_file_id")
    if not output_file_id:
        raise RuntimeError(f"OpenAI batch {batch_id} completed without output_file_id")
    output_text = download_openai_file_text(api_key, str(output_file_id))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_text, encoding="utf-8")
    return parse_batch_output(output_text)


def build_skill_batch_requests(
    snapshot_items: list[tuple[str, dict[str, Any]]],
    as_of: str,
    model: str,
    use_web: bool,
) -> list[dict[str, Any]]:
    requests = []
    for custom_id, snapshot in snapshot_items:
        requests.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": build_buffett_skill_response_payload(snapshot, as_of, model, use_web=use_web),
            }
        )
    return requests


def build_structuring_batch_requests(
    memos: dict[str, str],
    snapshots_by_id: dict[str, dict[str, Any]],
    as_of: str,
    model: str,
) -> list[dict[str, Any]]:
    requests = []
    for custom_id, memo in memos.items():
        snapshot = snapshots_by_id[custom_id]
        requests.append(
            {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": build_structured_response_payload(
                    build_buffett_structure_prompt(memo, snapshot, as_of),
                    model,
                ),
            }
        )
    return requests


def failed_analysis(ticker: str, snapshot: dict[str, Any], as_of: str, reason: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "is_good_company": False,
        "quality_score": None,
        "moat_assessment": "",
        "veto_reason": reason,
        "buy_zone_lower": None,
        "buy_zone_upper": None,
        "buy_zone_currency": snapshot.get("currency") or "USD",
        "buy_zone_basis": "",
        "buy_zone_as_of": as_of,
        "raw_analysis": {"error": reason},
    }


def run_buffett_skill_batch(
    jobs: list[tuple[dict[str, Any], dict[str, Any]]],
    output_dir: Path,
    as_of: str,
    skill_model: str,
    structuring_model: str,
    poll_seconds: int,
    timeout_seconds: int,
    use_web: bool = True,
) -> list[dict[str, Any]]:
    snapshots = [snapshot for _, snapshot in jobs]
    snapshot_items = [
        (f"{index:05d}_{batch_custom_id(str(snapshot.get('ticker', '')))}", snapshot)
        for index, snapshot in enumerate(snapshots)
    ]
    snapshots_by_id = dict(snapshot_items)
    skill_results = run_responses_batch(
        build_skill_batch_requests(snapshot_items, as_of, skill_model, use_web),
        output_dir,
        "buffett_skill",
        poll_seconds,
        timeout_seconds,
    )

    memos: dict[str, str] = {}
    analyses_by_id: dict[str, dict[str, Any]] = {}
    for custom_id, snapshot in snapshots_by_id.items():
        ticker = str(snapshot.get("ticker", "")).upper()
        payload = skill_results.get(custom_id, {})
        if payload.get("error"):
            analyses_by_id[custom_id] = failed_analysis(
                ticker,
                snapshot,
                as_of,
                f"buffett skill batch failed: {payload.get('error')}",
            )
            continue
        memo = response_text_from_openai_payload(payload)
        if not memo:
            analyses_by_id[custom_id] = failed_analysis(
                ticker,
                snapshot,
                as_of,
                f"buffett skill batch returned no memo: {json.dumps(payload)[:1000]}",
            )
            continue
        memos[custom_id] = memo

    structure_results = run_responses_batch(
        build_structuring_batch_requests(memos, snapshots_by_id, as_of, structuring_model),
        output_dir,
        "buffett_structure",
        poll_seconds,
        timeout_seconds,
    )

    for custom_id, memo in memos.items():
        snapshot = snapshots_by_id[custom_id]
        ticker = str(snapshot.get("ticker", "")).upper()
        payload = structure_results.get(custom_id, {})
        if payload.get("error"):
            analyses_by_id[custom_id] = failed_analysis(
                ticker,
                snapshot,
                as_of,
                f"buffett structuring batch failed: {payload.get('error')}",
            )
            analyses_by_id[custom_id]["raw_buffett_memo"] = memo
            continue
        text = response_text_from_openai_payload(payload)
        try:
            parsed = parse_buffett_analysis_output(text, ticker, as_of)
        except Exception as exc:
            parsed = failed_analysis(ticker, snapshot, as_of, f"buffett structuring parse failed: {type(exc).__name__}: {exc}")
        parsed["raw_buffett_memo"] = memo
        parsed["raw_openai_response_id"] = payload.get("id")
        parsed["gpt_model"] = skill_model
        parsed["structuring_model"] = structuring_model
        analyses_by_id[custom_id] = parsed

    ordered: list[dict[str, Any]] = []
    for custom_id, _ in snapshot_items:
        ordered.append(analyses_by_id[custom_id])
    return ordered


def run_local_buffett_analysis(snapshot: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Offline deterministic fallback for tests and smoke runs.

    This is not a replacement for the full Buffett skill. It exists so the CLI
    can be smoke-tested without network or an agent runtime.
    """
    info = snapshot.get("info") or {}
    fast_info = snapshot.get("fast_info") or {}
    source_roic = clean_float(snapshot.get("source_roic"))
    roe = clean_float(info.get("returnOnEquity"))
    operating_margin = clean_float(info.get("operatingMargins"))
    debt_to_equity = clean_float(info.get("debtToEquity"))
    free_cashflow = clean_float(info.get("freeCashflow"))
    shares = clean_float(info.get("sharesOutstanding") or fast_info.get("shares"))
    latest_close = clean_float(snapshot.get("latest_close") or info.get("currentPrice"))

    score = 0.0
    if source_roic is not None and source_roic >= 0.15:
        score += 30
    if roe is not None and roe >= 0.15:
        score += 25
    if operating_margin is not None and operating_margin >= 0.15:
        score += 20
    if debt_to_equity is None or debt_to_equity <= 100:
        score += 15
    if free_cashflow is not None and free_cashflow > 0:
        score += 10

    veto_reason = ""
    if debt_to_equity is not None and debt_to_equity > 250:
        veto_reason = "Debt load is too high for a Buffett-style quality pass."

    is_good = score >= 70 and not veto_reason
    intrinsic_per_share = None
    if free_cashflow and shares and shares > 0:
        intrinsic_per_share = (free_cashflow * 15) / shares
    elif latest_close and is_good:
        intrinsic_per_share = latest_close * 1.2

    lower = upper = None
    if intrinsic_per_share and intrinsic_per_share > 0:
        lower = intrinsic_per_share * 0.60
        upper = intrinsic_per_share * 0.75

    moat = "unknown"
    if score >= 85:
        moat = "strong/stable inferred from yfinance metrics"
    elif score >= 70:
        moat = "medium/stable inferred from yfinance metrics"

    return {
        "ticker": str(snapshot.get("ticker", "")).upper(),
        "is_good_company": is_good,
        "quality_score": round(score, 2),
        "moat_assessment": moat,
        "veto_reason": veto_reason,
        "key_assumptions": [
            "Offline smoke analysis uses yfinance metrics only.",
            "Full production analysis should use the buffett skill mode.",
        ],
        "buy_zone_lower": round(lower, 4) if lower is not None else None,
        "buy_zone_upper": round(upper, 4) if upper is not None else None,
        "buy_zone_currency": snapshot.get("currency") or "USD",
        "buy_zone_basis": "Owner-earnings proxy using 15x FCF with 25-40% margin of safety.",
        "buy_zone_as_of": as_of,
        "raw_analysis": {"mode": "local", "snapshot_status": snapshot.get("data_status")},
    }


def analysis_path(output_dir: Path, ticker: str) -> Path:
    return output_dir / "analysis" / f"{ticker}.json"


def write_analysis(output_dir: Path, analysis: dict[str, Any]) -> None:
    path = analysis_path(output_dir, str(analysis["ticker"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis, indent=2, default=json_default), encoding="utf-8")


def read_analysis(output_dir: Path, ticker: str) -> dict[str, Any] | None:
    path = analysis_path(output_dir, ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def merge_record(seed: dict[str, Any], snapshot: dict[str, Any], analysis: dict[str, Any], as_of: str) -> dict[str, Any]:
    zone = normalize_buy_zone_fields(analysis)
    latest_close = clean_float(snapshot.get("latest_close"))
    evaluation = evaluate_buy_zone(latest_close, zone.lower, zone.upper)
    is_good_company = is_truthy(analysis.get("is_good_company"))
    record = {
        **seed,
        "ticker": analysis.get("ticker") or snapshot.get("ticker") or seed.get("ticker"),
        "company_name": snapshot.get("company_name") or seed.get("company_name") or "",
        "sector": snapshot.get("sector") or seed.get("sector") or "",
        "industry": snapshot.get("industry") or seed.get("industry") or "",
        "is_good_company": is_good_company,
        "quality_score": normalize_quality_score(analysis.get("quality_score")),
        "moat_assessment": analysis.get("moat_assessment", ""),
        "veto_reason": analysis.get("veto_reason", ""),
        "buy_zone_lower": zone.lower,
        "buy_zone_upper": zone.upper,
        "buy_zone_currency": zone.currency or analysis.get("buy_zone_currency") or snapshot.get("currency") or "USD",
        "buy_zone_basis": zone.basis or analysis.get("buy_zone_basis") or "",
        "buy_zone_as_of": zone.as_of or analysis.get("buy_zone_as_of") or as_of,
        "latest_close": latest_close,
        "latest_close_date": snapshot.get("latest_close_date") or "",
        "buy_zone_status": evaluation.status,
        "distance_to_buy_zone_upper": evaluation.distance_to_buy_zone_upper,
        "actionable": evaluation.actionable and is_good_company,
        "next_earnings_date": snapshot.get("next_earnings_date") or "",
        "next_earnings_source": snapshot.get("next_earnings_source") or "missing",
        "last_earnings_date": snapshot.get("last_earnings_date") or "",
        "last_analysis_date": as_of,
        "needs_reanalysis": False,
    }
    return record


def needs_reanalysis(record: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    last_analysis = parse_date(record.get("last_analysis_date"))
    if last_analysis is None:
        return True

    last_earnings = parse_date(snapshot.get("last_earnings_date"))
    if last_earnings and last_earnings > last_analysis:
        return True
    if last_earnings and snapshot.get("last_earnings_has_actual"):
        record_last_earnings = parse_date(record.get("last_earnings_date"))
        if record_last_earnings is None or last_earnings != record_last_earnings:
            return True

    latest_statement = parse_date(snapshot.get("latest_quarterly_statement_date"))
    if latest_statement and latest_statement > last_analysis:
        return True
    return False


def needs_earnings_check(record: dict[str, Any], as_of: str) -> bool:
    next_earnings = parse_date(record.get("next_earnings_date"))
    if next_earnings is None:
        return True
    target = parse_date(as_of) or date.today()
    return next_earnings < target


def write_daily_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    write_csv(path, rows, SIGNAL_FIELDS)


def format_number(value: Any, digits: int = 2) -> str:
    number = clean_float(value)
    if number is None:
        return ""
    return str(round(number, digits))


def earnings_within_days(rows: list[dict[str, Any]], as_of: str, days: int) -> list[dict[str, Any]]:
    start = parse_date(as_of) or date.today()
    end = start + timedelta(days=days)
    upcoming: list[tuple[date, dict[str, Any]]] = []
    for row in rows:
        earnings_date = parse_date(row.get("next_earnings_date"))
        if earnings_date and start <= earnings_date <= end:
            upcoming.append((earnings_date, row))
    return [row for _, row in sorted(upcoming, key=lambda item: (item[0], str(item[1].get("ticker", ""))))]


def option_expiration_info(expirations: Any, as_of: str, days: int = OPTION_EXPIRATION_LOOKAHEAD_DAYS) -> dict[str, Any]:
    start = parse_date(as_of) or date.today()
    end = start + timedelta(days=days)
    expiration_dates: list[date] = []
    for value in expirations or []:
        expiration_date = parse_date(value)
        if expiration_date and start <= expiration_date <= end:
            expiration_dates.append(expiration_date)

    unique_dates = sorted(set(expiration_dates))
    return {
        "has_option_expiring_within_7d": bool(unique_dates),
        "next_option_expiration_date": unique_dates[0].isoformat() if unique_dates else "",
        "option_expirations_within_7d": ";".join(item.isoformat() for item in unique_dates),
    }


def format_option_expiration_status(row: dict[str, Any]) -> str:
    value = row.get("has_option_expiring_within_7d")
    if is_truthy(value):
        expiration = row.get("next_option_expiration_date") or row.get("option_expirations_within_7d") or ""
        return f"Yes ({expiration})" if expiration else "Yes"
    if str(value).strip().lower() in {"false", "0", "no", "n"} or value is False:
        return "No"
    return "Unknown"


def format_earnings_session(value: Any) -> str:
    return {
        "before_market": "盘前",
        "after_market": "盘后",
        "during_market": "盘中",
    }.get(str(value or "").strip(), "未知")


def load_good_company_rows(output_dir: Path) -> list[dict[str, str]]:
    return read_csv_rows(output_dir / "good_companies.csv")


def write_daily_summary(
    path: Path,
    rows: list[dict[str, Any]],
    as_of: str,
    earnings_rows: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    actionable = [row for row in rows if is_truthy(row.get("actionable")) and is_truthy(row.get("is_good_company"))]
    needs = [row for row in rows if str(row.get("needs_reanalysis")).lower() == "true" or row.get("needs_reanalysis") is True]
    reanalyzed = [row for row in rows if is_truthy(row.get("reanalyzed_today"))]
    earnings_scope = earnings_rows if earnings_rows is not None else rows
    upcoming_earnings = earnings_within_days(earnings_scope, as_of, 3)
    lines = [
        f"# Buffett Daily Monitor - {as_of}",
        "",
        f"- Companies monitored: {len(rows)}",
        f"- Actionable buy-zone signals: {len(actionable)}",
        f"- Companies needing reanalysis: {len(needs)}",
        f"- Companies reanalyzed with Buffett: {len(reanalyzed)}",
        f"- Earnings in next 3 days: {len(upcoming_earnings)}",
        "",
        "## Actionable",
        "",
    ]
    if actionable:
        lines.append("| Ticker | Close | Status | Buy Zone | Next Earnings |")
        lines.append("| --- | ---: | --- | ---: | --- |")
        for row in actionable:
            zone = f"{row.get('buy_zone_lower', '')}-{row.get('buy_zone_upper', '')}"
            lines.append(
                f"| {row.get('ticker', '')} | {format_number(row.get('latest_close', ''), 2)} | "
                f"{row.get('buy_zone_status', '')} | {zone} | {row.get('next_earnings_date', '')} |"
            )
    else:
        lines.append("No actionable buy-zone signals.")

    lines.extend(["", "## Earnings In Next 3 Days", ""])
    if upcoming_earnings:
        lines.append("| Ticker | Company | Earnings Date | Session | Options <=7d |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in upcoming_earnings:
            lines.append(
                f"| {row.get('ticker', '')} | {row.get('company_name', '')} | "
                f"{row.get('next_earnings_date', '')} | "
                f"{format_earnings_session(row.get('next_earnings_session'))} | "
                f"{format_option_expiration_status(row)} |"
            )
    else:
        lines.append("No tracked companies have earnings scheduled in the next 3 days.")

    lines.extend(["", "## Reanalyzed With Buffett", ""])
    if reanalyzed:
        lines.append("| Ticker | Company | Good Company | Quality Score |")
        lines.append("| --- | --- | --- | ---: |")
        for row in reanalyzed:
            lines.append(
                f"| {row.get('ticker', '')} | {row.get('company_name', '')} | "
                f"{row.get('is_good_company', '')} | {row.get('quality_score', '')} |"
            )
    else:
        lines.append("No companies were reanalyzed with Buffett today.")

    if needs:
        lines.extend(["", "## Needs Reanalysis", ""])
        for row in needs:
            lines.append(f"- {row.get('ticker', '')}: latest earnings/report data changed")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge_signal_rows(existing_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {
        str(row.get("ticker", "")).upper(): row
        for row in existing_rows
        if str(row.get("ticker", "")).strip()
    }
    for row in new_rows:
        ticker = str(row.get("ticker", "")).upper()
        if ticker:
            merged[ticker] = row
    return [merged[ticker] for ticker in sorted(merged)]


def analysis_matches_as_of(analysis: dict[str, Any], as_of: str) -> bool:
    snapshot = analysis.get("snapshot") if isinstance(analysis.get("snapshot"), dict) else {}
    if snapshot.get("data_status") in {"fetch_error", "missing_market_data"}:
        return False
    if analysis.get("last_analysis_date") == as_of:
        return True
    if analysis.get("buy_zone_as_of") == as_of:
        return True
    return snapshot.get("as_of") == as_of


def analyze_one(
    seed: dict[str, Any],
    adapter: YFinanceAdapter,
    output_dir: Path,
    as_of: str,
    analysis_mode: str,
    gpt_model: str = DEFAULT_GPT_MODEL,
    structuring_model: str = DEFAULT_STRUCTURING_MODEL,
    reuse_existing: bool = True,
) -> dict[str, Any]:
    ticker = str(seed["ticker"]).upper()
    existing = read_analysis(output_dir, ticker) if reuse_existing else None
    if existing and analysis_matches_as_of(existing, as_of):
        snapshot = existing.get("snapshot") if isinstance(existing.get("snapshot"), dict) else {}
        return merge_record(seed, snapshot, existing, as_of)

    snapshot = adapter.get_snapshot(ticker, as_of, seed)
    if analysis_mode == "gpt":
        try:
            analysis = run_buffett_gpt(snapshot, as_of, model=gpt_model)
        except Exception as exc:
            analysis = failed_analysis(ticker, snapshot, as_of, f"gpt analysis failed: {type(exc).__name__}: {exc}")
            analysis["raw_analysis"]["model"] = gpt_model
    elif analysis_mode == "skill":
        try:
            analysis = run_buffett_skill(snapshot, as_of, structuring_model=structuring_model)
        except Exception as exc:
            analysis = failed_analysis(ticker, snapshot, as_of, f"buffett skill failed: {type(exc).__name__}: {exc}")
    else:
        analysis = run_local_buffett_analysis(snapshot, as_of)
    persisted = {**analysis, "snapshot": snapshot, "last_analysis_date": as_of}
    write_analysis(output_dir, persisted)
    return merge_record(seed, snapshot, persisted, as_of)


def analyze_many(
    rows: list[dict[str, Any]],
    adapter: YFinanceAdapter,
    output_dir: Path,
    as_of: str,
    analysis_mode: str,
    gpt_model: str = DEFAULT_GPT_MODEL,
    structuring_model: str = DEFAULT_STRUCTURING_MODEL,
    reuse_existing: bool = True,
    poll_seconds: int = 30,
    timeout_seconds: int = 86400,
    use_web: bool = True,
) -> list[dict[str, Any]]:
    if analysis_mode != "skill":
        return [
            analyze_one(
                row,
                adapter,
                output_dir,
                as_of,
                analysis_mode,
                gpt_model=gpt_model,
                structuring_model=structuring_model,
                reuse_existing=reuse_existing,
            )
            for row in rows
        ]

    records_by_ticker: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        ticker = str(row["ticker"]).upper()
        existing = read_analysis(output_dir, ticker) if reuse_existing else None
        if existing and analysis_matches_as_of(existing, as_of):
            snapshot = existing.get("snapshot") if isinstance(existing.get("snapshot"), dict) else {}
            records_by_ticker[ticker] = merge_record(row, snapshot, existing, as_of)
            continue
        snapshot = adapter.get_snapshot(ticker, as_of, row)
        jobs.append((row, snapshot))

    if jobs:
        analyses = run_buffett_skill_batch(
            jobs,
            output_dir,
            as_of,
            skill_model=gpt_model,
            structuring_model=structuring_model,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            use_web=use_web,
        )
        for (row, snapshot), analysis in zip(jobs, analyses):
            persisted = {**analysis, "snapshot": snapshot, "last_analysis_date": as_of}
            write_analysis(output_dir, persisted)
            records_by_ticker[str(row["ticker"]).upper()] = merge_record(row, snapshot, persisted, as_of)

    return [records_by_ticker[str(row["ticker"]).upper()] for row in rows]


def command_build_watchlist(args: argparse.Namespace) -> int:
    rows = load_ticker_universe(args.tickers_csv)
    output = args.output_dir / "ticker_universe.csv"
    write_csv(output, rows, UNIVERSE_FIELDS)
    print(f"Wrote {len(rows)} tickers to {output}")
    return 0


def iter_limited(
    rows: list[dict[str, Any]],
    limit: int | None,
    tickers: list[str] | None,
    start: int = 1,
    end: int | None = None,
) -> list[dict[str, Any]]:
    if tickers:
        allowed = {ticker.upper() for ticker in tickers}
        rows = [row for row in rows if str(row.get("ticker", "")).upper() in allowed]
    if start < 1:
        raise ValueError("--start must be 1 or greater")
    start_index = start - 1
    end_index = end if end is not None else None
    rows = rows[start_index:end_index]
    if limit is not None:
        rows = rows[:limit]
    return rows


def merge_good_company_rows(
    existing_rows: list[dict[str, Any]],
    analyzed_rows: list[dict[str, Any]],
    processed_tickers: set[str],
) -> list[dict[str, Any]]:
    existing_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in existing_rows
        if str(row.get("ticker", "")).strip()
    }
    merged = dict(existing_by_ticker)
    for ticker in processed_tickers:
        merged.pop(ticker, None)
    for row in analyzed_rows:
        if is_truthy(row.get("is_good_company")):
            ticker = str(row.get("ticker", "")).upper()
            existing = existing_by_ticker.get(ticker, {})
            preserved_manual = {
                field: existing.get(field)
                for field in MANUAL_CALIBRATION_FIELDS
                if row.get(field) in (None, "") and existing.get(field) not in (None, "")
            }
            merged[ticker] = {**row, **preserved_manual}
    return [merged[ticker] for ticker in sorted(merged)]


def command_analyze_watchlist(args: argparse.Namespace) -> int:
    universe_path = args.output_dir / "ticker_universe.csv"
    rows = read_csv_rows(universe_path)
    if not rows:
        rows = load_ticker_universe(args.tickers_csv)
    rows = iter_limited(rows, args.limit, args.tickers, args.start, args.end)

    adapter = YFinanceAdapter(args.cache_dir, max_retries=args.max_retries, force_refresh=args.force_refresh)
    existing_good = load_good_company_rows(args.output_dir)
    processed: set[str] = set()
    records = analyze_many(
        rows,
        adapter,
        args.output_dir,
        args.as_of,
        args.analysis_mode,
        gpt_model=args.gpt_model,
        structuring_model=args.structuring_model,
        reuse_existing=not args.no_resume,
        poll_seconds=args.batch_poll_seconds,
        timeout_seconds=args.batch_timeout_seconds,
        use_web=not args.no_skill_web,
    )
    batch_good: list[dict[str, Any]] = []
    for index, (row, record) in enumerate(zip(rows, records), start=1):
        processed.add(str(row.get("ticker", "")).upper())
        if record.get("is_good_company"):
            batch_good.append(record)
        print(f"Analyzed {index}/{len(rows)} {row.get('ticker')}: good={record.get('is_good_company')}")

    merged_good = merge_good_company_rows(existing_good, batch_good, processed)
    write_csv(args.output_dir / "good_companies.csv", merged_good, GOOD_COMPANIES_FIELDS)
    print(f"Wrote {len(merged_good)} total good companies to {args.output_dir / 'good_companies.csv'}")
    return 0


def command_refresh_earnings_calendar(args: argparse.Namespace) -> int:
    from src.tools.earnings_calendar import command_refresh

    return command_refresh(args)


def command_daily_monitor(args: argparse.Namespace) -> int:
    existing_good = load_good_company_rows(args.output_dir)
    rows = iter_limited(existing_good, args.limit, args.tickers, args.start, args.end)
    adapter = YFinanceAdapter(args.cache_dir, max_retries=args.max_retries, force_refresh=args.force_refresh)
    signals: list[dict[str, Any]] = []
    updated_good: list[dict[str, Any]] = []
    needs_rows: list[dict[str, Any]] = []
    current_by_ticker: dict[str, dict[str, Any]] = {}

    daily_dir = args.output_dir / "daily"
    signals_path = daily_dir / f"{args.as_of}_signals.csv"
    summary_path = daily_dir / f"{args.as_of}_summary.md"
    checkpoint_path = daily_dir / f".{args.as_of}_signals.partial.csv"
    checkpoint_interval = max(1, int(getattr(args, "checkpoint_interval", 100) or 100))

    price_snapshots = adapter.get_price_snapshots(rows, args.as_of)
    earnings_check_rows = [row for row in rows if needs_earnings_check(row, args.as_of)]
    print(
        f"Refreshing prices for {len(rows)} companies; "
        f"checking earnings for {len(earnings_check_rows)} due/missing companies"
    )

    for index, row in enumerate(rows, start=1):
        ticker = str(row["ticker"]).upper()
        snapshot = price_snapshots.get(ticker, {})
        zone = normalize_buy_zone_fields(row)
        latest_close = clean_float(snapshot.get("latest_close"))
        evaluation = evaluate_buy_zone(latest_close, zone.lower, zone.upper)
        current_row = {
            **row,
            "latest_close": latest_close,
            "latest_close_date": snapshot.get("latest_close_date") or "",
            "buy_zone_status": evaluation.status,
            "distance_to_buy_zone_upper": evaluation.distance_to_buy_zone_upper,
            "actionable": evaluation.actionable,
            "needs_reanalysis": False,
            "reanalyzed_today": False,
        }
        signals.append(current_row)
        current_by_ticker[ticker] = current_row
        if index % checkpoint_interval == 0:
            write_daily_checkpoint(checkpoint_path, signals)

    write_daily_checkpoint(checkpoint_path, signals)

    for index, row in enumerate(earnings_check_rows, start=1):
        ticker = str(row["ticker"]).upper()
        snapshot = adapter.get_snapshot(ticker, args.as_of, row)
        needs = needs_reanalysis(row, snapshot)
        current_row = current_by_ticker[ticker]
        current_row.update(
            {
                "next_earnings_date": snapshot.get("next_earnings_date") or row.get("next_earnings_date", ""),
                "next_earnings_source": snapshot.get("next_earnings_source") or row.get("next_earnings_source", "missing"),
                "last_earnings_date": snapshot.get("last_earnings_date") or row.get("last_earnings_date", ""),
                "needs_reanalysis": needs,
            }
        )
        if needs and not args.skip_reanalysis:
            needs_rows.append(current_row)
        if index % checkpoint_interval == 0:
            write_daily_checkpoint(checkpoint_path, signals)

    write_daily_checkpoint(checkpoint_path, signals)

    reanalyzed_by_ticker: dict[str, dict[str, Any]] = {}
    if needs_rows:
        print(f"Reanalyzing {len(needs_rows)} companies; unchanged companies will only refresh prices/earnings")
        for record in analyze_many(
            needs_rows,
            adapter,
            args.output_dir,
            args.as_of,
            args.analysis_mode,
            gpt_model=args.gpt_model,
            structuring_model=args.structuring_model,
            reuse_existing=False,
            poll_seconds=args.batch_poll_seconds,
            timeout_seconds=args.batch_timeout_seconds,
            use_web=not args.no_skill_web,
        ):
            reanalyzed_by_ticker[str(record.get("ticker", "")).upper()] = record

    final_signals: list[dict[str, Any]] = []
    for index, row in enumerate(signals, start=1):
        ticker = str(row["ticker"]).upper()
        current_row = row
        if ticker in reanalyzed_by_ticker:
            current_row = reanalyzed_by_ticker[ticker]
            current_row["reanalyzed_today"] = True
        else:
            current_row["reanalyzed_today"] = False
        final_signals.append(current_row)
        if is_truthy(current_row.get("is_good_company")):
            updated_good.append(current_row)
        if index % checkpoint_interval == 0:
            write_daily_checkpoint(checkpoint_path, final_signals)

    write_daily_checkpoint(checkpoint_path, final_signals)
    processed = {str(row.get("ticker", "")).upper() for row in rows if str(row.get("ticker", "")).strip()}
    merged_good = merge_good_company_rows(existing_good, updated_good, processed)
    merged_signals = merge_signal_rows(read_csv_rows(signals_path), final_signals)
    current_good_tickers = {
        str(row.get("ticker", "")).upper()
        for row in merged_good
        if is_truthy(row.get("is_good_company"))
    }
    merged_signals = [
        row
        for row in merged_signals
        if str(row.get("ticker", "")).upper() in current_good_tickers
    ]
    write_csv(signals_path, merged_signals, SIGNAL_FIELDS)
    earnings_calendar_rows = read_csv_rows(args.output_dir / "earnings_calendar.csv")
    if earnings_calendar_rows:
        try:
            from src.tools.earnings_calendar import (
                DEFAULT_CALENDAR_FILENAME,
                EARNINGS_CALENDAR_FIELDS,
                EarningsCalendarAdapter,
                confirm_upcoming_earnings_sessions,
                update_checkpoint_for_remaining_confirmations,
            )
        except ModuleNotFoundError:
            from earnings_calendar import (
                DEFAULT_CALENDAR_FILENAME,
                EARNINGS_CALENDAR_FIELDS,
                EarningsCalendarAdapter,
                confirm_upcoming_earnings_sessions,
                update_checkpoint_for_remaining_confirmations,
            )

        calendar_adapter = EarningsCalendarAdapter(
            args.cache_dir,
            max_retries=args.max_retries,
            force_refresh=args.force_refresh,
        )
        calendar_checkpoint_path = args.output_dir / f".{DEFAULT_CALENDAR_FILENAME}.partial"
        if calendar_checkpoint_path.exists():
            earnings_calendar_rows = read_csv_rows(calendar_checkpoint_path)
        earnings_calendar_rows, confirmed_sessions = confirm_upcoming_earnings_sessions(
            earnings_calendar_rows,
            calendar_adapter,
            args.as_of,
            3,
            checkpoint_path=calendar_checkpoint_path,
            checkpoint_interval=checkpoint_interval,
            confirmation_limit=args.confirmation_limit,
        )
        write_csv(args.output_dir / "earnings_calendar.csv", earnings_calendar_rows, EARNINGS_CALENDAR_FIELDS)
        remaining_calendar_confirmations = update_checkpoint_for_remaining_confirmations(
            earnings_calendar_rows,
            calendar_checkpoint_path,
            args.as_of,
            3,
        )
        print(f"Confirmed earnings sessions for {len(confirmed_sessions)} companies in the next 3 days")
        print(f"Remaining unconfirmed upcoming earnings rows: {remaining_calendar_confirmations}")
    write_daily_summary(summary_path, merged_signals, args.as_of, earnings_calendar_rows or None)
    write_csv(args.output_dir / "good_companies.csv", merged_good, GOOD_COMPANIES_FIELDS)
    checkpoint_path.unlink(missing_ok=True)
    print(f"Wrote daily signals to {signals_path}")
    print(f"Wrote daily summary to {summary_path}")
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    errors: list[str] = []
    try:
        import yfinance  # noqa: F401
    except Exception as exc:
        errors.append(f"yfinance import failed: {type(exc).__name__}: {exc}")
    if args.analysis_mode in {"gpt", "skill"}:
        try:
            import httpx  # noqa: F401
        except Exception as exc:
            errors.append(f"httpx import failed: {type(exc).__name__}: {exc}")
        if not openai_api_key():
            errors.append("OPENAI_API_KEY is not set; GPT/skill mode cannot call OpenAI")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Preflight passed for {args.analysis_mode} mode")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Analysis date in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--tickers", nargs="*", help="Optional ticker subset.")
    parser.add_argument("--start", type=int, default=1, help="1-based start row in the selected CSV/list.")
    parser.add_argument("--end", type=int, default=None, help="1-based inclusive end row in the selected CSV/list.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max row count for smoke runs.")
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--force-refresh", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yfinance-only Buffett research pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-watchlist")
    build.add_argument("--tickers-csv", type=Path, default=DEFAULT_TICKERS_CSV)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    build.set_defaults(func=command_build_watchlist)

    analyze = subparsers.add_parser("analyze-watchlist")
    add_common_args(analyze)
    analyze.add_argument("--tickers-csv", type=Path, default=DEFAULT_TICKERS_CSV)
    analyze.add_argument("--analysis-mode", choices=["gpt", "skill", "local"], default="gpt")
    analyze.add_argument("--gpt-model", default=DEFAULT_GPT_MODEL)
    analyze.add_argument("--structuring-model", default=DEFAULT_STRUCTURING_MODEL)
    analyze.add_argument("--batch-poll-seconds", type=int, default=30)
    analyze.add_argument("--batch-timeout-seconds", type=int, default=86400)
    analyze.add_argument("--no-skill-web", action="store_true", help="Disable web search for skill-mode batch requests.")
    analyze.add_argument("--no-resume", action="store_true", help="Ignore cached per-ticker analysis JSON.")
    analyze.set_defaults(func=command_analyze_watchlist)

    earnings = subparsers.add_parser("refresh-earnings-calendar")
    add_common_args(earnings)
    earnings.add_argument("--tickers-csv", type=Path, default=DEFAULT_TICKERS_CSV)
    earnings.add_argument("--refresh-mode", choices=["stale", "due", "all"], default="due")
    earnings.add_argument("--max-age-days", type=int, default=1)
    earnings.add_argument("--checkpoint-interval", type=int, default=100)
    earnings.add_argument("--confirmation-limit", type=int, default=200)
    earnings.add_argument("--report-days", type=int, default=3)
    earnings.set_defaults(func=command_refresh_earnings_calendar)

    monitor = subparsers.add_parser("daily-monitor")
    add_common_args(monitor)
    monitor.add_argument("--analysis-mode", choices=["gpt", "skill", "local"], default="gpt")
    monitor.add_argument("--gpt-model", default=DEFAULT_GPT_MODEL)
    monitor.add_argument("--structuring-model", default=DEFAULT_STRUCTURING_MODEL)
    monitor.add_argument("--batch-poll-seconds", type=int, default=30)
    monitor.add_argument("--batch-timeout-seconds", type=int, default=86400)
    monitor.add_argument("--no-skill-web", action="store_true", help="Disable web search for skill-mode batch requests.")
    monitor.add_argument("--skip-reanalysis", action="store_true")
    monitor.add_argument("--checkpoint-interval", type=int, default=100)
    monitor.add_argument("--confirmation-limit", type=int, default=200)
    monitor.set_defaults(func=command_daily_monitor)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--analysis-mode", choices=["gpt", "skill", "local"], default="gpt")
    preflight.set_defaults(func=command_preflight)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
