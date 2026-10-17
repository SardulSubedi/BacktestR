from __future__ import annotations

import csv
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import zipfile

from backtestr.codegen import export_bundle, preview_source


def spec(**changes):
    values = dict(name="A strategy", description="A test", symbols=["AAA"], benchmark="SPY",
                  signal="buy_hold", fast_window=2, slow_window=3, lookback=2,
                  entry_threshold=0.0, exit_threshold=0.0, initial_cash=1000.0,
                  max_position_weight=1.0, rebalance="daily", commission_bps=0,
                  slippage_bps=0, start_date="2024-01-02", end_date="2024-01-05")
    values.update(changes)
    return values


def dataset():
    bars = {}
    for symbol in ("AAA", "SPY"):
        bars[symbol] = [dict(date=f"2024-01-0{i}", open=10.0, high=11.0, low=9.0, close=10.0, volume=1000) for i in range(2, 6)]
    return dict(bars=bars, source="unit fixture", synthetic=True, adjustment="none", warnings=["Synthetic"])


class ExportTests(unittest.TestCase):
    def test_bundle_is_deterministic_and_contains_buildable_project(self):
        first = export_bundle(spec(), dataset())
        self.assertEqual(first, export_bundle(spec(), dataset()))
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertTrue({"strategy.hpp", "main.cpp", "alpaca_paper.cpp", "CMakeLists.txt", "README.md", "strategy.json", "bars.csv", "data_provenance.json", "tests/core_tests.cpp"}.issubset(archive.namelist()))
            self.assertNotIn(b"@GENERATED_CONFIGURATION@", archive.read("strategy.hpp"))
            self.assertEqual(json.loads(archive.read("data_provenance.json"))["synthetic"], True)
            rows = list(csv.DictReader(io.StringIO(archive.read("bars.csv").decode())))
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows[0]["symbol"], "AAA")

    def test_source_escapes_user_text_and_rejects_code_in_symbols(self):
        dangerous = '"; system("bad"); //\\\nemoji \U0001f680'
        source = preview_source(spec(name=dangerous))
        self.assertIn(r"\042; system(\042bad\042); //\134\012emoji ", source)
        self.assertNotIn('kName = ""; system', source)
        with self.assertRaises(ValueError):
            export_bundle(spec(symbols=['AAA";system("bad")']))

    def test_rejects_nonfinite_unsafe_or_unsupported_values(self):
        for changes in ({"initial_cash": float("nan")}, {"max_position_weight": 2}, {"signal": "eval"}, {"fast_window": True}, {"symbols": ["AAA", "AAA"]}, {"start_date": "2024-02-30"}, {"signal": "rsi", "entry_threshold": 70, "exit_threshold": 30}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                export_bundle(spec(**changes))

    def test_paper_endpoint_is_fixed_and_submission_is_explicit(self):
        with zipfile.ZipFile(io.BytesIO(export_bundle(spec()))) as archive:
            paper = archive.read("alpaca_paper.cpp").decode()
            self.assertIn('https://paper-api.alpaca.markets', paper)
            self.assertNotIn('https://api.alpaca.markets', paper)
            self.assertIn('--submit-paper', paper)
            self.assertIn('create_directory(claim)', paper)
            self.assertIn('status=open', paper)
            self.assertIn('client_order_id', paper)
            self.assertLess(paper.index('std::getenv("APCA_API_KEY_ID")'), paper.index('std::getenv("ALPACA_API_KEY")'))
            self.assertIn('last_session + "T23%3A59%3A59Z', paper)

    def test_optional_csv_string_preserved(self):
        content = "date,symbol,open,high,low,close,volume\n2024-01-02,AAA,1,1,1,1,1\n"
        with zipfile.ZipFile(io.BytesIO(export_bundle(spec(), content))) as archive:
            self.assertEqual(archive.read("bars.csv").decode(), content)
            self.assertIn('kHistoryStart = "2024-01-02"', archive.read("strategy.hpp").decode())


_DISCOVERED_COMPILER = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")
_COMPILER = shlex.split(os.environ["CXX"]) if os.environ.get("CXX") else ([_DISCOVERED_COMPILER] if _DISCOVERED_COMPILER else [])


@unittest.skipUnless(_COMPILER, "C++20 compiler not available; CI compiles and replays exports")
class CompiledExportTests(unittest.TestCase):
    def _compile_bundle(self, folder, strategy, data):
        with zipfile.ZipFile(io.BytesIO(export_bundle(strategy, data))) as archive:
            archive.extractall(folder)
        executable = Path(folder) / ("replay.exe" if os.name == "nt" else "replay")
        subprocess.run([*_COMPILER, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Wpedantic", str(Path(folder) / "main.cpp"), "-o", str(executable)], check=True, capture_output=True, text=True)
        core_test = Path(folder) / ("core_tests.exe" if os.name == "nt" else "core_tests")
        subprocess.run([*_COMPILER, "-std=c++20", "-O2", "-I", str(folder), str(Path(folder) / "tests" / "core_tests.cpp"), "-o", str(core_test)], check=True, capture_output=True, text=True)
        subprocess.run([str(core_test)], check=True, capture_output=True, text=True)
        return executable

    def test_replay_next_open_costs_and_missing_calendar_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = self._compile_bundle(folder, spec(commission_bps=100, slippage_bps=100), dataset())
            output = subprocess.run([str(executable), str(Path(folder) / "bars.csv"), "--trades", str(Path(folder) / "trades.csv")], check=True, capture_output=True, text=True)
            result = json.loads(output.stdout)
            # 98 shares cost 10 * 1.01 * 1.01 each; mark at $10.
            self.assertEqual(result["positions"]["AAA"], 98)
            self.assertAlmostEqual(result["final_equity"], 1000 - 98 * 10 * 1.01 * 1.01 + 980)
            with (Path(folder) / "trades.csv").open() as handle:
                first = next(csv.DictReader(handle))
                self.assertEqual((first["signal_date"], first["date"]), ("2024-01-02", "2024-01-03"))
            csv_path = Path(folder) / "bars.csv"
            csv_path.write_text(csv_path.read_text().replace("2024-01-05,SPY,10.0,11.0,9.0,10.0,1000\n", ""))
            bad = subprocess.run([str(executable), str(csv_path)], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("identical calendars", bad.stderr)

    def test_cpp_matches_python_engine_for_supported_signals(self):
        from backtestr.engine import run_backtest
        bars = {}
        start = date(2024, 1, 1)
        for symbol, shift in (("AAA", 0), ("BBB", 1), ("SPY", 3)):
            series = []
            for i in range(85):
                close = 30 + shift + (i % 13 - 6) * 0.4 + i * 0.05
                opening = close + ((i % 3) - 1) * 0.2
                series.append(dict(date=(start + timedelta(days=i)).isoformat(), open=opening, high=max(close, opening) + 0.5, low=min(close, opening) - 0.5, close=close, volume=10000))
            bars[symbol] = series
        data = dict(bars=bars, source="parity fixture", synthetic=True, adjustment="none", warnings=[])
        for signal in ("buy_hold", "sma_cross", "momentum", "rsi"):
            strategy = spec(symbols=["BBB", "AAA"], signal=signal, start_date="2024-01-15", end_date="2024-03-20", initial_cash=10000, commission_bps=3, slippage_bps=5,
                            entry_threshold=35 if signal == "rsi" else 0.01, exit_threshold=65 if signal == "rsi" else 0)
            with self.subTest(signal=signal), tempfile.TemporaryDirectory() as folder:
                executable = self._compile_bundle(folder, strategy, data)
                actual = json.loads(subprocess.run([str(executable), str(Path(folder) / "bars.csv")], check=True, capture_output=True, text=True).stdout)
                expected = run_backtest(strategy, data)
                # Engine public response uses metrics/equity; this assertion also
                # makes changes to either execution contract visible in CI.
                self.assertAlmostEqual(actual["final_equity"], expected["metrics"]["final_equity"], places=6)

    def test_flat_decimal_sma_does_not_invent_a_crossover(self):
        start = date(2024, 1, 1)
        bars = {symbol: [dict(date=(start + timedelta(days=i)).isoformat(), open=0.1, high=0.1, low=0.1, close=0.1, volume=1000) for i in range(120)] for symbol in ("AAA", "SPY")}
        data = dict(bars=bars, source="flat decimal fixture", synthetic=True, adjustment="none", warnings=[])
        strategy = spec(signal="sma_cross", fast_window=3, slow_window=5, start_date="2024-01-10", end_date="2024-04-20")
        with tempfile.TemporaryDirectory() as folder:
            executable = self._compile_bundle(folder, strategy, data)
            actual = json.loads(subprocess.run([str(executable), str(Path(folder) / "bars.csv")], check=True, capture_output=True, text=True).stdout)
            self.assertEqual(actual["trade_count"], 0)
            self.assertEqual(actual["final_equity"], strategy["initial_cash"])


if __name__ == "__main__":
    unittest.main()
