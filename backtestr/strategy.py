"""The version-one strategy intermediate representation shared by every engine.

All rates use fractions except commission/slippage, which use basis points.
The validator deliberately rejects unknown fields instead of dropping rules.
"""

from __future__ import annotations

from datetime import date
import math
import re


SIGNALS = ("sma_cross", "rsi", "momentum", "buy_hold")
REBALANCES = ("daily", "weekly", "monthly")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?\Z")
_INTEGERS = ("fast_window", "slow_window", "lookback")
_NUMBERS = ("entry_threshold", "exit_threshold", "initial_cash", "max_position_weight", "commission_bps", "slippage_bps")


def default_spec() -> dict:
    """A concrete example, never an implicit replacement for a user's strategy."""
    return {
        "name": "Daily SMA 20 / 100", "description": "Equal-weight AAPL and MSFT while the 20-day simple moving average exceeds the 100-day simple moving average.",
        "symbols": ["AAPL", "MSFT"], "benchmark": "SPY", "signal": "sma_cross",
        "fast_window": 20, "slow_window": 100, "lookback": 14,
        "entry_threshold": 30.0, "exit_threshold": 70.0,
        "initial_cash": 100000.0, "max_position_weight": 0.5,
        "rebalance": "daily", "commission_bps": 1.0, "slippage_bps": 5.0,
        "start_date": "2020-01-01", "end_date": "2025-12-31",
    }


def validate_spec(spec: dict) -> dict:
    """Validate a complete IR and return an independent, canonical JSON object."""
    if not isinstance(spec, dict):
        raise ValueError("Strategy must be a JSON object.")
    expected = set(default_spec())
    missing, extra = expected - spec.keys(), spec.keys() - expected
    if missing:
        raise ValueError("Missing strategy fields: " + ", ".join(sorted(missing)))
    if extra:
        raise ValueError("Unknown strategy fields (rules cannot be silently discarded): " + ", ".join(sorted(extra)))
    result = dict(spec)
    for field, limit in (("name", 120), ("description", 12000)):
        if not isinstance(result[field], str) or not result[field].strip() or len(result[field]) > limit:
            raise ValueError(f"{field} must be nonempty text of at most {limit} characters.")
        result[field] = result[field].strip()
    if not isinstance(result["symbols"], list) or not 1 <= len(result["symbols"]) <= 50:
        raise ValueError("symbols must contain between 1 and 50 explicit stock tickers.")
    normalized = []
    for symbol in result["symbols"]:
        if not isinstance(symbol, str) or len(symbol.strip()) > 10 or not _SYMBOL.fullmatch(symbol.strip().upper()):
            raise ValueError("Each symbol must be a stock ticker such as AAPL or BRK.B.")
        normalized.append(symbol.strip().upper())
    if len(set(normalized)) != len(normalized):
        raise ValueError("symbols must not contain duplicates.")
    result["symbols"] = normalized
    if not isinstance(result["benchmark"], str) or len(result["benchmark"].strip()) > 10 or not _SYMBOL.fullmatch(result["benchmark"].strip().upper()):
        raise ValueError("benchmark must be one stock/ETF ticker.")
    result["benchmark"] = result["benchmark"].strip().upper()
    if result["signal"] not in SIGNALS:
        raise ValueError("signal must be sma_cross, rsi, momentum, or buy_hold.")
    if result["rebalance"] not in REBALANCES:
        raise ValueError("rebalance must be daily, weekly, or monthly.")
    for field in _INTEGERS:
        if type(result[field]) is not int or not 1 <= result[field] <= 2520:
            raise ValueError(f"{field} must be an integer from 1 to 2520 trading days.")
    for field in _NUMBERS:
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"{field} must be a finite number.")
        try:
            result[field] = float(value)
        except OverflowError:
            raise ValueError(f"{field} is outside the supported numeric range.") from None
    if not 1 <= result["initial_cash"] <= 1e12:
        raise ValueError("initial_cash must be between $1 and $1 trillion.")
    if not 0 < result["max_position_weight"] <= 1:
        raise ValueError("max_position_weight must be a fraction greater than 0 and at most 1.")
    for field in ("commission_bps", "slippage_bps"):
        if not 0 <= result[field] <= 1000:
            raise ValueError(f"{field} must be between 0 and 1000 basis points.")
    if result["signal"] == "sma_cross" and result["fast_window"] >= result["slow_window"]:
        raise ValueError("SMA fast_window must be smaller than slow_window.")
    if result["signal"] == "rsi" and not 0 <= result["entry_threshold"] < result["exit_threshold"] <= 100:
        raise ValueError("RSI requires 0 <= entry_threshold < exit_threshold <= 100.")
    if result["signal"] == "momentum" and not -1 <= result["entry_threshold"] <= 100:
        raise ValueError("Momentum entry_threshold is a return fraction between -1 and 100.")
    parsed_dates = []
    for field in ("start_date", "end_date"):
        value = result[field]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD).")
        try:
            parsed_dates.append(date.fromisoformat(value))
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid calendar date.") from exc
    if parsed_dates[0] >= parsed_dates[1]:
        raise ValueError("start_date must precede end_date.")
    return result


def strategy_schema(*, nullable: bool = False) -> dict:
    """JSON schema accepted by Responses API strict structured outputs."""
    properties = {}
    example = default_spec()
    for field, value in example.items():
        kind = "array" if isinstance(value, list) else "integer" if field in _INTEGERS else "number" if field in _NUMBERS else "string"
        item = {"type": [kind, "null"] if nullable else kind}
        if kind == "array":
            item["items"] = {"type": "string"}
        if field in ("signal", "rebalance"):
            item["enum"] = list(SIGNALS if field == "signal" else REBALANCES) + ([None] if nullable else [])
        properties[field] = item
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
