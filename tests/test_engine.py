import copy
import json
import unittest
from datetime import date, timedelta

from backtestr.engine import run_backtest


def strategy(**changes):
    result = {"name": "Test", "description": "", "symbols": ["AAPL"], "benchmark": "SPY", "signal": "buy_hold", "fast_window": 2, "slow_window": 3, "lookback": 2, "entry_threshold": 0, "exit_threshold": 0, "initial_cash": 1000, "max_position_weight": 1, "rebalance": "daily", "commission_bps": 0, "slippage_bps": 0, "start_date": "2024-01-04", "end_date": "2024-01-09"}
    result.update(changes)
    return result


def dataset(prices=None, opens=None, count=6):
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    if count != 6:
        current = date(2020, 1, 1)
        days = []
        while len(days) < count:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current += timedelta(days=1)
    prices = prices or [10] * count
    opens = opens or prices
    rows = [{"date": day, "open": opening, "close": closing, "high": max(opening, closing), "low": min(opening, closing), "volume": 10000} for day, opening, closing in zip(days, opens, prices)]
    return {"bars": {"AAPL": rows, "SPY": copy.deepcopy(rows)}, "source": "unit test", "synthetic": False, "adjustment": "split", "warnings": []}


class ExecutionTests(unittest.TestCase):
    def test_next_open_not_signal_close_and_initial_cash_baseline(self):
        data = dataset(prices=[10, 11, 12, 30, 40, 50], opens=[10, 11, 12, 20, 40, 50])
        report = run_backtest(strategy(), data)
        self.assertEqual(report["equity"][0]["equity"], 1000)
        self.assertEqual(report["trades"][0]["date"], "2024-01-05")
        self.assertEqual(report["trades"][0]["price"], 20)
        self.assertEqual(report["trades"][0]["shares"], 50)
        self.assertEqual(report["equity"][1]["equity"], 1500)
        self.assertEqual(report["metrics"]["total_return"], 1.5)
        self.assertEqual(report["equity"][-1]["benchmark"], 2500)

    def test_commission_slippage_and_affordability_are_accounted(self):
        report = run_backtest(strategy(commission_bps=100, slippage_bps=100), dataset())
        buy = report["trades"][0]
        self.assertEqual(buy["shares"], 98)
        self.assertAlmostEqual(buy["price"], 10.1)
        self.assertAlmostEqual(buy["commission"], 9.898)
        self.assertAlmostEqual(buy["slippage"], 9.8)
        self.assertAlmostEqual(report["cash"], 0.302)
        self.assertAlmostEqual(report["equity"][1]["equity"], 980.302)
        self.assertAlmostEqual(report["metrics"]["max_drawdown"], -0.019698)
        self.assertGreaterEqual(report["cash"], 0)

    def test_sma_does_not_use_execution_day_close_for_signal(self):
        data = dataset(prices=[10, 10, 8, 30, 30, 30], opens=[10, 10, 8, 5, 10, 30])
        report = run_backtest(strategy(signal="sma_cross", fast_window=1, slow_window=3), data)
        self.assertEqual(report["trades"][0]["date"], "2024-01-08")
        self.assertEqual(report["trades"][0]["price"], 10)

    def test_flat_decimal_sma_does_not_invent_a_crossover(self):
        for fast, slow in ((20, 100), (3, 5)):
            for price in (0.1, 0.2, 1.1, 10.3):
                with self.subTest(fast=fast, slow=slow, price=price):
                    data = dataset(prices=[price] * 120, count=120)
                    days = [row["date"] for row in data["bars"]["AAPL"]]
                    report = run_backtest(strategy(signal="sma_cross", fast_window=fast, slow_window=slow, start_date=days[100], end_date=days[-1]), data)
                    self.assertEqual(report["trades"], [])
                    self.assertEqual(report["metrics"]["total_return"], 0)

    def test_changing_future_prices_does_not_change_prefix(self):
        data = dataset(prices=[10, 12, 14, 13, 9, 11])
        revised = copy.deepcopy(data)
        for rows in revised["bars"].values():
            rows[-1].update(open=100, high=200, close=200, low=100)
        first = run_backtest(strategy(signal="sma_cross"), data)
        second = run_backtest(strategy(signal="sma_cross"), revised)
        self.assertEqual(first["equity"][:-1], second["equity"][:-1])
        self.assertEqual([row for row in first["trades"] if row["date"] < "2024-01-09"], [row for row in second["trades"] if row["date"] < "2024-01-09"])

    def test_rsi_state_carries_through_neutral_values(self):
        # RSI at Jan 4 = 0 (enter); Jan 5 = 50 (hold); Jan 8 = 100 (exit).
        data = dataset(prices=[12, 10, 8, 10, 12, 12], opens=[12, 10, 8, 10, 12, 12])
        report = run_backtest(strategy(signal="rsi", entry_threshold=30, exit_threshold=70), data)
        self.assertEqual([(row["date"], row["side"]) for row in report["trades"]], [("2024-01-05", "buy"), ("2024-01-09", "sell")])
        self.assertEqual(report["portfolio"], [])

    def test_flat_rsi_is_neutral(self):
        report = run_backtest(strategy(signal="rsi", entry_threshold=30, exit_threshold=70), dataset())
        self.assertEqual(report["trades"], [])
        self.assertEqual(report["metrics"]["total_return"], 0)
        self.assertIsNone(report["metrics"]["sharpe"])

    def test_rsi_flat_window_has_no_residual_from_prior_price_moves(self):
        prices = [830.6308482906352, 808.5858232353002, 802.4433601575365, 201.50126183904763, 316.7514577265402, 630.7058463889988, 734.5757617899308, 856.1018744114338]
        prices += [prices[-1]] * 5
        data = dataset(prices=prices, count=len(prices))
        days = [row["date"] for row in data["bars"]["AAPL"]]
        report = run_backtest(strategy(signal="rsi", lookback=3, entry_threshold=55, exit_threshold=70, start_date=days[3], end_date=days[-1]), data)
        # Once all moves leave the window RSI is 50, which must enter at <=55.
        self.assertTrue(report["portfolio"])
        self.assertEqual(report["trades"][-1]["date"], days[11])
        self.assertEqual(report["trades"][-1]["side"], "buy")

    def test_momentum_threshold_is_a_fraction(self):
        data = dataset(prices=[100, 100, 104, 106, 106, 106])
        report = run_backtest(strategy(signal="momentum", entry_threshold=0.05), data)
        self.assertEqual(report["trades"][0]["date"], "2024-01-08")

    def test_position_cap_leaves_cash(self):
        report = run_backtest(strategy(max_position_weight=0.25), dataset())
        self.assertEqual(report["portfolio"][0]["shares"], 25)
        self.assertEqual(report["cash"], 750)
        self.assertEqual(report["portfolio"][0]["weight"], 0.25)

    def test_weekly_signal_exits_only_on_scheduled_open(self):
        data = dataset(prices=[8, 9, 10, 2, 2, 2])
        report = run_backtest(strategy(signal="sma_cross", fast_window=1, slow_window=3, rebalance="weekly"), data)
        self.assertEqual([(row["date"], row["side"]) for row in report["trades"]], [("2024-01-05", "buy"), ("2024-01-08", "sell")])

    def test_multiple_symbols_sell_before_buy(self):
        data = dataset(prices=[10, 11, 12, 8, 7, 6])
        data["bars"]["MSFT"] = dataset(prices=[10, 9, 8, 12, 13, 14])["bars"]["AAPL"]
        report = run_backtest(strategy(symbols=["MSFT", "AAPL"], signal="sma_cross", fast_window=1, slow_window=3), data)
        jan8 = [row for row in report["trades"] if row["date"] == "2024-01-08"]
        self.assertEqual([(row["symbol"], row["side"]) for row in jan8], [("AAPL", "sell"), ("MSFT", "buy")])
        self.assertGreaterEqual(report["cash"], 0)


class ValidationAndRobustnessTests(unittest.TestCase):
    def test_insufficient_warmup_rejected(self):
        with self.assertRaisesRegex(ValueError, "warmup"):
            run_backtest(strategy(signal="sma_cross", slow_window=5), dataset())

    def test_missing_benchmark_or_calendar_rejected(self):
        data = dataset()
        data["bars"]["SPY"].pop()
        with self.assertRaisesRegex(ValueError, "calendars"):
            run_backtest(strategy(), data)
        del data["bars"]["SPY"]
        with self.assertRaisesRegex(ValueError, "Missing bars"):
            run_backtest(strategy(), data)

    def test_invalid_spec_rejected(self):
        for change in ({"initial_cash": float("nan")}, {"initial_cash": 10 ** 1000}, {"max_position_weight": 1.1}, {"signal": "magic"}, {"rebalance": "hourly"}, {"symbols": ["AAPL", "aapl"]}, {"fast_window": True}, {"signal": "rsi", "entry_threshold": 80, "exit_threshold": 20}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_backtest(strategy(**change), dataset())

    def test_missing_requested_history_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "cover"):
            run_backtest(strategy(start_date="2023-01-01"), dataset())

    def test_bootstrap_is_reproducible_and_json_is_finite(self):
        data = dataset(prices=[100 + 0.02 * i + (i % 11) for i in range(600)], count=600)
        days = [row["date"] for row in data["bars"]["AAPL"]]
        spec = strategy(start_date=days[50], end_date=days[-1], max_position_weight=0.9)
        first = run_backtest(spec, data)
        second = run_backtest(spec, data)
        self.assertEqual(first, second)
        json.dumps(first, allow_nan=False)
        bootstrap = first["robustness"]["bootstrap"]
        self.assertLessEqual(bootstrap["lower_return"], bootstrap["upper_return"])
        self.assertTrue(first["robustness"]["rolling"])
        split = first["robustness"]["split"]
        self.assertEqual(split["training"]["end_date"], split["holdout"]["start_date"])
        self.assertEqual(split["holdout"]["initial_cash"], 1000)

    def test_synthetic_flag_cannot_be_suppressed(self):
        data = dataset()
        data["synthetic"] = True
        report = run_backtest(strategy(), data)
        self.assertTrue(any("SYNTHETIC" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
