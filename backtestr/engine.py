"""Causal daily-bar, long-only research simulator using only the standard library.

Signals observe completed closes; orders execute at the following open. A backtest
is a conditional simulation, never a guarantee of executable or future returns.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
import math
import random
import statistics

from .data import _iso_date, _symbol, validate_dataset


def _number(spec: dict, name: str, default: float, minimum: float, maximum: float) -> float:
    value = spec.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number between {minimum} and {maximum}.")
    return float(value)


def _integer(spec: dict, name: str, default: int) -> int:
    value = spec.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2520:
        raise ValueError(f"{name} must be an integer from 1 to 2520.")
    return value


def _validate_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Strategy must be an object.")
    raw_symbols = spec.get("symbols")
    if not isinstance(raw_symbols, list) or not 1 <= len(raw_symbols) <= 50:
        raise ValueError("A strategy requires 1–50 symbols.")
    result = dict(spec)
    result["symbols"] = [_symbol(symbol) for symbol in raw_symbols]
    if len(set(result["symbols"])) != len(result["symbols"]):
        raise ValueError("Strategy symbols must be unique.")
    result["symbols"].sort()
    result["benchmark"] = _symbol(spec.get("benchmark", "SPY"))
    if spec.get("signal") not in {"sma_cross", "rsi", "momentum", "buy_hold"}:
        raise ValueError("Unsupported signal; use sma_cross, rsi, momentum, or buy_hold.")
    result["signal"] = spec["signal"]
    for name, default in (("fast_window", 20), ("slow_window", 50), ("lookback", 14)):
        result[name] = _integer(spec, name, default)
    if result["signal"] == "sma_cross" and result["fast_window"] >= result["slow_window"]:
        raise ValueError("fast_window must be smaller than slow_window.")
    result["entry_threshold"] = _number(spec, "entry_threshold", 30 if result["signal"] == "rsi" else 0, -1e308, 1e308)
    result["exit_threshold"] = _number(spec, "exit_threshold", 70 if result["signal"] == "rsi" else 0, -1e308, 1e308)
    if result["signal"] == "rsi" and not 0 <= result["entry_threshold"] < result["exit_threshold"] <= 100:
        raise ValueError("RSI thresholds require 0 <= entry_threshold < exit_threshold <= 100.")
    if result["signal"] == "momentum" and not -1 <= result["entry_threshold"] <= 100:
        raise ValueError("Momentum entry_threshold is a return fraction between -1 and 100.")
    result["initial_cash"] = _number(spec, "initial_cash", 100_000, 1, 1_000_000_000_000)
    result["max_position_weight"] = _number(spec, "max_position_weight", 1, 0, 1)
    if result["max_position_weight"] <= 0:
        raise ValueError("max_position_weight must be greater than zero.")
    result["commission_bps"] = _number(spec, "commission_bps", 1, 0, 1000)
    result["slippage_bps"] = _number(spec, "slippage_bps", 5, 0, 1000)
    if spec.get("rebalance", "daily") not in {"daily", "weekly", "monthly"}:
        raise ValueError("rebalance must be daily, weekly, or monthly.")
    result["rebalance"] = spec.get("rebalance", "daily")
    result["start_date"] = _iso_date(spec.get("start_date"), "start_date")
    result["end_date"] = _iso_date(spec.get("end_date"), "end_date")
    if result["start_date"] >= result["end_date"]:
        raise ValueError("start_date must precede end_date.")
    return result


def _signals(spec: dict, bars: dict) -> dict[str, list[bool]]:
    output = {}
    for symbol in spec["symbols"]:
        closes = [row["close"] for row in bars[symbol]]
        active = [False] * len(closes)
        if spec["signal"] == "buy_hold":
            active = [True] * len(closes)
        elif spec["signal"] == "sma_cross":
            fast, slow = spec["fast_window"], spec["slow_window"]
            for index, closing in enumerate(closes):
                if index >= slow - 1:
                    # Center both means on the same close. This cancels the shared
                    # price level before comparison, keeping flat windows exactly
                    # equal rather than inventing a crossover from rounding drift.
                    fast_mean = math.fsum(closes[j] - closing for j in range(index - fast + 1, index + 1)) / fast
                    slow_mean = math.fsum(closes[j] - closing for j in range(index - slow + 1, index + 1)) / slow
                    active[index] = fast_mean > slow_mean
        elif spec["signal"] == "momentum":
            lookback = spec["lookback"]
            for index in range(lookback, len(closes)):
                active[index] = closes[index] / closes[index - lookback] - 1 > spec["entry_threshold"]
        else:
            lookback = spec["lookback"]
            gains = [0.0] + [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
            losses = [0.0] + [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
            gain_sum = loss_sum = 0.0
            gain_count = loss_count = 0
            holding = False
            for index in range(1, len(closes)):
                gain_sum += gains[index]
                loss_sum += losses[index]
                gain_count += gains[index] > 0
                loss_count += losses[index] > 0
                if index > lookback:
                    gain_sum -= gains[index - lookback]
                    loss_sum -= losses[index - lookback]
                    gain_count -= gains[index - lookback] > 0
                    loss_count -= losses[index - lookback] > 0
                if gain_count == 0:
                    gain_sum = 0.0
                if loss_count == 0:
                    loss_sum = 0.0
                if index >= lookback:
                    # Small cancellation errors must not turn a flat window into an extreme RSI.
                    gain = max(0.0, gain_sum)
                    loss = max(0.0, loss_sum)
                    rsi = (50.0 if gain == 0 else 100.0) if loss == 0 else 100 - 100 / (1 + gain / loss)
                    if not holding and rsi <= spec["entry_threshold"]:
                        holding = True
                    elif holding and rsi >= spec["exit_threshold"]:
                        holding = False
                    active[index] = holding
        output[symbol] = active
    return output


def _metrics(equity: list[dict], initial_cash: float, trades: list[dict]) -> dict:
    final = equity[-1]["equity"]
    returns = [b["equity"] / a["equity"] - 1 for a, b in zip(equity, equity[1:])]
    years = (date.fromisoformat(equity[-1]["date"]) - date.fromisoformat(equity[0]["date"])).days / 365.25
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    growth = math.log(final / initial_cash) / years if years > 0 and final > 0 else None
    return {
        "initial_cash": initial_cash,
        "final_equity": final,
        "total_return": final / initial_cash - 1,
        "cagr": math.expm1(growth) if growth is not None and growth < 700 else None,
        "sharpe": statistics.mean(returns) / std * math.sqrt(252) if std > 1e-15 else None,
        "volatility": std * math.sqrt(252),
        "max_drawdown": min(row["drawdown"] for row in equity),
        "trade_count": len(trades),
        "benchmark_return": equity[-1]["benchmark"] / initial_cash - 1,
        "excess_return": (final - equity[-1]["benchmark"]) / initial_cash,
        "total_commission": math.fsum(trade["commission"] for trade in trades),
        "total_slippage": math.fsum(trade["slippage"] for trade in trades),
        "trading_days": len(returns),
    }


def _shares(value: float) -> int:
    if not math.isfinite(value) or value < 0 or value > 9_223_372_036_854_775_807:
        raise ValueError("Simulated share count exceeds the supported numeric range; reduce capital or check prices.")
    return math.floor(value)


def _simulate(spec: dict, bars: dict, days: list[str], active: dict, lo: int, hi: int, cost_multiplier: float = 1.0) -> dict:
    cash = spec["initial_cash"]
    positions = {symbol: 0 for symbol in spec["symbols"]}
    benchmark_cash = cash
    benchmark_shares = 0
    commission_rate = spec["commission_bps"] / 10_000 * cost_multiplier
    slippage_rate = spec["slippage_bps"] / 10_000 * cost_multiplier
    trades = []
    equity = []
    peak = cash
    for index in range(lo, hi + 1):
        if index > lo:
            if index == lo + 1:
                benchmark_open = bars[spec["benchmark"]][index]["open"]
                benchmark_unit = benchmark_open * (1 + slippage_rate) * (1 + commission_rate)
                benchmark_shares = _shares(benchmark_cash / benchmark_unit)
                benchmark_cash -= benchmark_shares * benchmark_unit
            day = date.fromisoformat(days[index])
            previous = date.fromisoformat(days[index - 1])
            scheduled = index == lo + 1 or spec["rebalance"] == "daily"
            scheduled |= spec["rebalance"] == "weekly" and day.isocalendar()[:2] != previous.isocalendar()[:2]
            scheduled |= spec["rebalance"] == "monthly" and (day.year, day.month) != (previous.year, previous.month)
            if scheduled:
                opens = {symbol: bars[symbol][index]["open"] for symbol in positions}
                opening_equity = cash + math.fsum(positions[symbol] * opens[symbol] for symbol in positions)
                if not math.isfinite(opening_equity) or opening_equity <= 0:
                    raise ValueError("Simulation exceeded its numeric range; check the supplied price history.")
                selected = [symbol for symbol in positions if active[symbol][index - 1]]
                weight = min(1 / len(selected), spec["max_position_weight"]) if selected else 0.0
                targets = {symbol: _shares(opening_equity * weight / opens[symbol]) if symbol in selected else 0 for symbol in positions}
                for side in ("sell", "buy"):
                    for symbol in positions:
                        difference = targets[symbol] - positions[symbol]
                        if (side == "sell" and difference >= 0) or (side == "buy" and difference <= 0):
                            continue
                        fill = opens[symbol] * (1 - slippage_rate if side == "sell" else 1 + slippage_rate)
                        quantity = -difference if side == "sell" else min(difference, _shares(max(0.0, cash) / (fill * (1 + commission_rate))))
                        if quantity <= 0:
                            continue
                        notional = quantity * fill
                        fee = notional * commission_rate
                        if side == "sell":
                            positions[symbol] -= quantity
                            cash += notional - fee
                        else:
                            positions[symbol] += quantity
                            cash -= notional + fee
                            if -1e-7 < cash < 0:
                                cash = 0.0
                        trades.append({"date": days[index], "symbol": symbol, "side": side, "shares": quantity, "price": fill, "commission": fee, "slippage": quantity * abs(fill - opens[symbol]), "reason": "Rebalance using previous completed close"})
        marked = cash + math.fsum(positions[symbol] * bars[symbol][index]["close"] for symbol in positions)
        benchmark_value = benchmark_cash + benchmark_shares * bars[spec["benchmark"]][index]["close"]
        if not math.isfinite(marked) or not math.isfinite(benchmark_value) or marked <= 0:
            raise ValueError("Simulation exceeded its numeric range; check the supplied price history.")
        peak = max(peak, marked)
        equity.append({"date": days[index], "equity": marked, "benchmark": benchmark_value, "drawdown": marked / peak - 1})
    final = equity[-1]["equity"]
    portfolio = []
    for symbol in positions:
        if positions[symbol]:
            price = bars[symbol][hi]["close"]
            value = positions[symbol] * price
            portfolio.append({"symbol": symbol, "shares": positions[symbol], "price": price, "value": value, "weight": value / final})
    return {"metrics": _metrics(equity, spec["initial_cash"], trades), "equity": equity, "trades": trades, "portfolio": portfolio, "cash": cash}


def _window_report(label: str, result: dict) -> dict:
    return {"label": label, "start_date": result["equity"][0]["date"], "end_date": result["equity"][-1]["date"], **result["metrics"]}


def _bootstrap(equity: list[dict]) -> dict:
    returns = [b["equity"] / a["equity"] - 1 for a, b in zip(equity, equity[1:])]
    details = {"method": "Seeded moving-block bootstrap of observed net daily returns", "seed": 1729, "samples": 400, "confidence": 0.90, "block_days": min(20, len(returns)), "lower_return": None, "upper_return": None}
    if len(returns) < 40:
        details["note"] = "At least 40 daily returns are required; no interval is reported for this short sample."
        return details
    rng = random.Random(details["seed"])
    block = details["block_days"]
    samples = []
    for _ in range(details["samples"]):
        log_return = 0.0
        remaining = len(returns)
        while remaining:
            offset = rng.randrange(0, len(returns) - block + 1)
            take = min(remaining, block)
            log_return += math.fsum(math.log1p(value) for value in returns[offset:offset + take])
            remaining -= take
        if log_return >= 700:
            details["note"] = "Resampled growth exceeds the numeric range; no interval is reported. Check extreme prices and sample length."
            return details
        samples.append(math.expm1(log_return))
    samples.sort()
    details["lower_return"] = samples[math.floor((len(samples) - 1) * 0.05)]
    details["upper_return"] = samples[math.ceil((len(samples) - 1) * 0.95)]
    details["note"] = "A resampling interval conditional on this history and strategy; it is not a forecast, significance test, or protection against selection bias."
    return details


def run_backtest(spec: dict, dataset: dict) -> dict:
    """Validate, simulate, and evaluate fixed strategy parameters on supplied bars.

    Returns are fractions (0.10 means 10%); drawdowns are nonpositive fractions.
    The requested start close is a cash baseline and first signal observation;
    the first order can fill only at the following common trading day's open.
    """
    spec = _validate_spec(spec)
    dataset = validate_dataset(dataset)
    bars = dataset["bars"]
    required = sorted(set(spec["symbols"] + [spec["benchmark"]]))
    for symbol in required:
        if symbol not in bars:
            raise ValueError(f"Missing bars for required symbol {symbol}.")
    days = [row["date"] for row in bars[required[0]]]
    for symbol in required[1:]:
        if [row["date"] for row in bars[symbol]] != days:
            raise ValueError(f"Trading calendars do not align for {symbol}; supply matching daily bars for every strategy symbol and benchmark. Missing prices are never forward filled.")
    lo = bisect_left(days, spec["start_date"])
    hi = bisect_right(days, spec["end_date"]) - 1
    if lo >= len(days) or hi <= lo:
        raise ValueError("Requested period requires at least two aligned trading dates.")
    if (date.fromisoformat(days[lo]) - date.fromisoformat(spec["start_date"])).days > 7 or (date.fromisoformat(spec["end_date"]) - date.fromisoformat(days[hi])).days > 7:
        raise ValueError("Dataset does not cover the requested date range; supply the full range plus warmup history.")
    warmup = spec["slow_window"] - 1 if spec["signal"] == "sma_cross" else spec["lookback"] if spec["signal"] in {"rsi", "momentum"} else 0
    if lo < warmup:
        raise ValueError(f"Insufficient warmup: {spec['signal']} needs {warmup} aligned bars before the first test date, but only {lo} are available.")
    active = _signals(spec, bars)
    result = _simulate(spec, bars, days, active, lo, hi)
    span = hi - lo
    split_index = lo + math.floor(span * 0.70)
    split = {"method": "Chronological 70/30 evaluation; fixed parameters, fresh initial cash in each window, no automatic fitting or selection", "training": None, "holdout": None}
    if split_index > lo and hi > split_index:
        split["training"] = _window_report("First 70%", _simulate(spec, bars, days, active, lo, split_index))
        split["holdout"] = _window_report("Final 30%", _simulate(spec, bars, days, active, split_index, hi))
    annual = []
    index = lo
    while index <= hi:
        end = index
        while end < hi and days[end + 1][:4] == days[index][:4]:
            end += 1
        if end > index:
            annual.append(_window_report(days[index][:4], _simulate(spec, bars, days, active, index, end)))
        index = end + 1
    rolling = []
    for index in range(lo, hi - 252 + 1, 63):
        rolling.append(_window_report("252-session window", _simulate(spec, bars, days, active, index, index + 252)))
    costs = []
    for multiplier in (2, 3):
        costs.append({"multiplier": multiplier, **_simulate(spec, bars, days, active, lo, hi, multiplier)["metrics"]})
    result["robustness"] = {"split": split, "annual": annual, "rolling": rolling, "cost_stress": costs, "bootstrap": _bootstrap(result["equity"])}
    warnings = list(dataset["warnings"])
    warnings.extend([
        "Daily bars cannot establish low-latency execution quality. Simulated market fills use the next open plus fixed slippage, with no spread model, partial fills, market impact, or capacity/volume limits.",
        "Returns exclude taxes, cash interest, borrow, financing, and explicit cash dividend payments. Dividend-adjusted input prices can embed approximate dividend returns; split adjusted prices exclude them. Final positions remain open and are valued at the final close; no final liquidation cost is charged.",
        "The universe is fixed. Current ticker selection, missing delisted securities, data revisions, and repeated strategy trials can create survivorship, selection, and overfitting bias.",
        "Chronological holdout uses unchanged parameters; it is only an unseen holdout if the user did not design or revise the strategy using that period. Annual and rolling windows restart with fresh cash and retain prior indicator history.",
        "Risk statistics annualize using 252 sessions and a zero risk-free rate; short samples are unstable. Bootstrap intervals resample observed returns and do not predict future performance.",
        "Integer-share allocation buys alphabetically after selling; cash constraints can favor earlier symbols. Position caps apply at rebalance opens and can drift between rebalances.",
        "The first test close is a cash baseline; first fills occur at the next open. The benchmark buys integer shares at that same open with the same modeled costs and holds its residual cash.",
    ])
    if spec["signal"] == "rsi":
        warnings.append("RSI uses simple rolling gains/losses, not Wilder smoothing; its state starts inactive at the first computable supplied historical bar. The extent of warmup history can affect initial state.")
    if dataset["adjustment"] in {"unknown", "raw"}:
        warnings.append("Input prices are not confirmed split adjusted. Corporate actions can create false signals and returns; verify consistent adjustments across every OHLC field before interpreting the study.")
    if span < 252:
        warnings.append("Fewer than 252 return observations are available; no 252-session rolling evaluation is possible and annualized metrics are especially fragile.")
    if dataset["synthetic"] and not any("SYNTHETIC" in item for item in warnings):
        warnings.insert(0, "SYNTHETIC DATA: results are a software demonstration, not evidence of investment performance.")
    result["warnings"] = list(dict.fromkeys(warnings))
    result["metadata"] = {"source": dataset["source"], "synthetic": dataset["synthetic"], "adjustment": dataset["adjustment"], "requested_start": spec["start_date"], "requested_end": spec["end_date"], "start_date": days[lo], "end_date": days[hi], "warmup_bars": lo, "required_warmup_bars": warmup, "signal_timing": "Completed close t -> open t+1", "return_units": "fraction", "benchmark": spec["benchmark"], "validation": "Fixed-parameter diagnostic evaluation; no profitability verdict or automatic optimization"}
    return result
