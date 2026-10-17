"""Explicitly labelled demo data, validated CSV imports, and Alpaca daily bars.

No provider is contacted by the demo or CSV paths. Provider credentials stay in
the server environment and are never included in a returned dataset.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
import math
import os
import random
import re
import time
from urllib import error, parse, request


DEMO_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ")
MAX_ROWS = 100_000
MAX_CSV_BYTES = 20_000_000
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume")


def _iso_date(value: object, label: str = "date") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD).") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO date (YYYY-MM-DD).")
    return value


def _symbol(value: object) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value.strip().upper()):
        raise ValueError("Symbols must contain 1–10 letters, digits, periods, or hyphens, starting with a letter.")
    return value.strip().upper()


def _bar(value: dict, context: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{context}: each bar must be an object.")
    result = {"date": _iso_date(value.get("date"), f"{context} date")}
    for name in ("open", "high", "low", "close", "volume"):
        try:
            if isinstance(value.get(name), bool):
                raise ValueError
            number = float(value[name])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{context}: {name} must be a finite number.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{context}: {name} must be a finite number.")
        if name == "volume":
            if number < 0 or not number.is_integer():
                raise ValueError(f"{context}: volume must be a nonnegative integer.")
            result[name] = int(number)
        else:
            if not 0.000001 <= number <= 1_000_000_000_000:
                raise ValueError(f"{context}: prices must be between 0.000001 and 1 trillion to stay within the simulator's numeric range.")
            result[name] = number
    if result["low"] > min(result["open"], result["close"]) or result["high"] < max(result["open"], result["close"]) or result["low"] > result["high"]:
        raise ValueError(f"{context}: OHLC prices are inconsistent.")
    return result


def validate_dataset(dataset: dict) -> dict:
    """Normalize a bounded dataset without silently filling missing observations."""
    if not isinstance(dataset, dict) or not isinstance(dataset.get("bars"), dict) or not dataset["bars"]:
        raise ValueError("Dataset must contain a nonempty bars object keyed by symbol.")
    normalized = {}
    count = 0
    for raw_symbol, raw_bars in dataset["bars"].items():
        symbol = _symbol(raw_symbol)
        if symbol in normalized:
            raise ValueError(f"Duplicate normalized symbol: {symbol}.")
        if not isinstance(raw_bars, list) or not raw_bars:
            raise ValueError(f"No bars supplied for {symbol}.")
        count += len(raw_bars)
        if count > MAX_ROWS:
            raise ValueError(f"Dataset exceeds {MAX_ROWS:,} bars.")
        rows = [_bar(bar, f"{symbol} row {i + 1}") for i, bar in enumerate(raw_bars)]
        rows.sort(key=lambda row: row["date"])
        if any(a["date"] == b["date"] for a, b in zip(rows, rows[1:])):
            raise ValueError(f"Duplicate date for {symbol}.")
        normalized[symbol] = rows
    raw_warnings = dataset.get("warnings", [])
    if not isinstance(raw_warnings, list) or any(not isinstance(item, str) for item in raw_warnings):
        raise ValueError("Dataset warnings must be a list of strings.")
    if not isinstance(dataset.get("synthetic", False), bool):
        raise ValueError("Dataset synthetic flag must be true or false.")
    return {
        "bars": normalized,
        "source": str(dataset.get("source", "User supplied data")),
        "synthetic": dataset.get("synthetic", False),
        "adjustment": str(dataset.get("adjustment", "unknown")),
        "warnings": list(raw_warnings),
    }


def parse_csv(text: str, adjustment: str = "unknown") -> dict:
    """Read long-format daily OHLCV, optionally preserving a synthetic column.

    Optional ``synthetic`` cells must be ``true`` or ``false`` on every row.
    A dataset containing any synthetic rows remains labelled synthetic as a whole.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("CSV is empty.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("CSV exceeds the 20 MB import limit.")
    if adjustment not in {"unknown", "raw", "split", "all", "split_adjusted", "total_return"}:
        raise ValueError("Unsupported adjustment label.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(_COLUMNS).issubset(reader.fieldnames):
        raise ValueError("CSV requires unique columns: date,symbol,open,high,low,close,volume.")
    bars: dict[str, list] = {}
    synthetic = False
    try:
        for index, row in enumerate(reader, start=2):
            if index - 1 > MAX_ROWS:
                raise ValueError(f"CSV exceeds {MAX_ROWS:,} bars.")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV row {index} has the wrong number of fields.")
            if "synthetic" in row:
                marker = row["synthetic"].strip().lower()
                if marker not in {"true", "false"}:
                    raise ValueError(f"CSV row {index}: synthetic must be true or false.")
                synthetic |= marker == "true"
            symbol = _symbol(row["symbol"])
            bars.setdefault(symbol, []).append(_bar(row, f"CSV row {index}"))
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}.") from exc
    warnings = ["CSV provenance, symbol history, corporate actions, and survivorship bias are the uploader's responsibility."]
    if synthetic:
        warnings.insert(0, "SYNTHETIC DATA: this CSV contains synthetic observations. Results demonstrate software behavior and are not evidence of historical investment performance.")
    if adjustment in {"unknown", "raw"}:
        warnings.append("Prices are not confirmed split adjusted; splits can create false signals and returns. Use consistently split adjusted OHLC data for meaningful analysis.")
    if adjustment in {"all", "total_return"}:
        warnings.append("Dividend-adjusted prices are a research approximation; historical adjusted fills are not actual executable prices.")
    return validate_dataset({"bars": bars, "source": "Uploaded CSV (contains synthetic data)" if synthetic else "Uploaded CSV", "synthetic": synthetic, "adjustment": adjustment, "warnings": warnings})


def demo_dataset() -> dict:
    """Deterministic synthetic weekdays, deliberately unrelated to actual quotes."""
    current = date(2014, 1, 2)
    end = date(2026, 10, 16)
    days = []
    while current <= end:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    market_rng = random.Random(4401)
    market = []
    for index in range(len(days)):
        regime = (index // 350) % 5
        drift = (0.00045, -0.00025, 0.00015, 0.0007, -0.0001)[regime]
        volatility = (0.008, 0.017, 0.006, 0.011, 0.020)[regime]
        market.append(market_rng.gauss(drift, volatility))
    output = {}
    for symbol_index, symbol in enumerate(DEMO_SYMBOLS):
        rng = random.Random(900 + symbol_index)
        previous = (45, 70, 22, 85, 170, 90)[symbol_index]
        beta = (1.05, 0.9, 1.5, 1.2, 0.8, 1.05)[symbol_index]
        bars = []
        for index, day in enumerate(days):
            gap = rng.gauss(0, 0.0025)
            opening = previous * math.exp(gap)
            daily = beta * market[index] + rng.gauss(0.00004, 0.004 if symbol in {"SPY", "QQQ"} else 0.009)
            closing = opening * math.exp(daily)
            spread = abs(rng.gauss(0.004, 0.002))
            high = max(opening, closing) * (1 + spread)
            low = min(opening, closing) / (1 + spread)
            bars.append({"date": day, "open": round(opening, 6), "high": round(high, 6), "low": round(low, 6), "close": round(closing, 6), "volume": rng.randint(1_000_000, 30_000_000)})
            previous = closing
        output[symbol] = bars
    return {
        "bars": output,
        "source": "Deterministic synthetic demonstration (seed 4401)",
        "synthetic": True,
        "adjustment": "synthetic",
        "warnings": [
            "SYNTHETIC DATA: ticker labels are placeholders, not historical stock prices. Results demonstrate software behavior and provide no evidence that a strategy works.",
            "Synthetic weekdays include exchange holidays and omit corporate actions, delistings, and real liquidity constraints.",
        ],
    }


def _completed_history_cutoff(now: datetime | None = None) -> date:
    """Conservative US daily-bar cutoff without a timezone/calendar dependency.

    Current UTC-day bars are excluded. Before 02:00 UTC, exclude yesterday too:
    the preceding US extended session and delayed data can still be updating.
    This intentionally favors completed history over the freshest possible bar.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.date() - timedelta(days=2 if current.hour < 2 else 1)


def fetch_alpaca(spec: dict) -> dict:
    """Download split adjusted daily bars from Alpaca; never submits orders.

    API contract: https://docs.alpaca.markets/us/reference/stockbars
    IEX is the default feed; SIP requires an appropriate market-data entitlement.
    The finite request/page limits intentionally fail instead of using partial data.
    """
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Alpaca data requires APCA_API_KEY_ID and APCA_API_SECRET_KEY in the server environment.")
    symbols = sorted({_symbol(value) for value in list(spec.get("symbols", [])) + [spec.get("benchmark", "SPY")]})
    if not symbols or len(symbols) > 51:
        raise ValueError("Alpaca downloads support 1–51 symbols including the benchmark.")
    start = date.fromisoformat(_iso_date(spec.get("start_date"), "start_date"))
    end = date.fromisoformat(_iso_date(spec.get("end_date"), "end_date"))
    if start >= end or (end - start).days > 365 * 30:
        raise ValueError("Alpaca date range must be increasing and at most 30 years.")
    effective_end = min(end, _completed_history_cutoff())
    if start >= effective_end:
        raise ValueError("The requested period does not yet contain two completed daily bars. Choose earlier dates.")
    feed = os.environ.get("ALPACA_DATA_FEED", "iex").lower()
    if feed not in {"iex", "sip"}:
        raise ValueError("ALPACA_DATA_FEED must be iex or sip.")
    try:
        signal = spec.get("signal", "sma_cross")
        warmup = int(spec.get("slow_window", 50)) if signal == "sma_cross" else int(spec.get("lookback", 14)) if signal in {"momentum", "rsi"} else 1
    except (ValueError, TypeError) as exc:
        raise ValueError("Strategy windows must be integers.") from exc
    if not 1 <= warmup <= 2520:
        raise ValueError("Strategy windows must be between 1 and 2520.")
    fetch_start = start - timedelta(days=warmup * 2 + 45)
    params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": fetch_start.isoformat(), "end": effective_end.isoformat() + "T23:59:59Z", "limit": "10000", "adjustment": "split", "feed": feed, "sort": "asc"}
    output: dict[str, list] = {symbol: [] for symbol in symbols}
    seen_tokens = set()
    deadline = time.monotonic() + 90
    for page in range(30):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Alpaca data request exceeded its 90 second time limit; use a smaller range.")
        req = request.Request("https://data.alpaca.markets/v2/stocks/bars?" + parse.urlencode(params), headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"})
        try:
            with request.urlopen(req, timeout=min(20, remaining)) as response:
                raw = response.read(15_000_001)
            if len(raw) > 15_000_000:
                raise ValueError("Alpaca returned an oversized response.")
            payload = json.loads(raw)
        except error.HTTPError as exc:
            raise ValueError(f"Alpaca data request failed (HTTP {exc.code}); check credentials, data entitlements, and request range.") from None
        except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ValueError("Alpaca data could not be downloaded; check the connection and retry.") from None
        raw_bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(raw_bars, dict):
            raise ValueError("Alpaca returned an invalid bars response.")
        for symbol, rows in raw_bars.items():
            if symbol not in output or not isinstance(rows, list):
                raise ValueError("Alpaca returned unexpected symbol data.")
            for row in rows:
                try:
                    output[symbol].append({"date": str(row["t"])[:10], "open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"], "volume": row["v"]})
                except (KeyError, TypeError):
                    raise ValueError("Alpaca returned an incomplete price bar.") from None
                if output[symbol][-1]["date"] > effective_end.isoformat():
                    raise ValueError("Alpaca returned a bar after the completed-history cutoff.")
        if sum(map(len, output.values())) > MAX_ROWS:
            raise ValueError(f"Downloaded data exceeds {MAX_ROWS:,} bars; use fewer symbols or a smaller range.")
        token = payload.get("next_page_token")
        if not token:
            break
        if not isinstance(token, str) or token in seen_tokens:
            raise ValueError("Alpaca returned an invalid repeated page token.")
        seen_tokens.add(token)
        params["page_token"] = token
    else:
        raise ValueError("Alpaca exceeded the 30 page limit; use a smaller range.")
    warnings = [
        "Split adjusted price returns exclude dividends, taxes, cash interest, and spin-off distributions. Adjusted historical fills are a research approximation.",
        "IEX is a single-exchange feed and may differ from consolidated market data." if feed == "iex" else "SIP feed availability depends on the market-data subscription.",
        "A fixed current-symbol universe can have survivorship and selection bias; delisted securities and historical constituents are not reconstructed.",
    ]
    if effective_end < end:
        warnings.append(f"Completed-history cutoff: requested end_date {end.isoformat()}, effective provider end_date {effective_end.isoformat()}. Current UTC-day bars are excluded, and the preceding UTC date is also excluded before 02:00 UTC. This conservative rule avoids unfinished daily bars; later data revisions remain possible.")
    return validate_dataset({"bars": output, "source": f"Alpaca historical daily bars ({feed.upper()} feed)", "synthetic": False, "adjustment": "split", "warnings": warnings})
