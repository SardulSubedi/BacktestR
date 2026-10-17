"""Deterministic, dependency-free C++20 strategy bundle generation.

The exported replay core never evaluates source supplied by a user or an LLM.
Only a validated strategy's typed, allowlisted parameters enter generated code.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any, Mapping
import zipfile


_TEMPLATES = Path(__file__).resolve().parent / "templates" / "cpp"
_SIGNALS = {"sma_cross": "SmaCross", "rsi": "Rsi", "momentum": "Momentum", "buy_hold": "BuyHold"}


def _plain_spec(spec: Any) -> dict[str, Any]:
    if hasattr(spec, "to_dict"):
        spec = spec.to_dict()
    elif hasattr(spec, "model_dump"):
        spec = spec.model_dump()
    elif not isinstance(spec, Mapping) and hasattr(spec, "__dataclass_fields__"):
        from dataclasses import asdict
        spec = asdict(spec)
    if not isinstance(spec, Mapping):
        raise ValueError("A validated strategy mapping is required.")
    from .strategy import validate_spec
    result = validate_spec(dict(spec))
    result["symbols"] = sorted(result["symbols"])
    return result


def _cpp_string(value: str) -> str:
    """Encode UTF-8 as bounded octal escapes, safe even for quotes and emoji."""
    return '"' + "".join(chr(byte) if 32 <= byte < 127 and byte not in (34, 92) else f"\\{byte:03o}" for byte in value.encode("utf-8")) + '"'


def _configuration(spec: Mapping[str, Any], history_start: str = "2010-01-01") -> str:
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()[:16]
    values = [
        f"inline constexpr std::string_view kName = {_cpp_string(spec['name'])};",
        f"inline constexpr std::string_view kStrategyId = \"{fingerprint}\";",
        f"inline constexpr std::array<std::string_view, {len(spec['symbols'])}> kSymbols = {{{', '.join(_cpp_string(s) for s in spec['symbols'])}}};",
        f"inline constexpr std::string_view kBenchmark = {_cpp_string(spec['benchmark'])};",
        f"inline constexpr Signal kSignal = Signal::{_SIGNALS[spec['signal']]};",
        f"inline constexpr std::string_view kRebalance = {_cpp_string(spec['rebalance'])};",
        f"inline constexpr std::string_view kStartDate = {_cpp_string(spec['start_date'])};",
        f"inline constexpr std::string_view kEndDate = {_cpp_string(spec['end_date'])};",
        f"inline constexpr std::string_view kHistoryStart = {_cpp_string(history_start)};",
    ]
    for field, cpp_name in (("fast_window", "kFastWindow"), ("slow_window", "kSlowWindow"), ("lookback", "kLookback")):
        values.append(f"inline constexpr std::size_t {cpp_name} = {spec[field]};")
    for field, cpp_name in (("entry_threshold", "kEntryThreshold"), ("exit_threshold", "kExitThreshold"), ("initial_cash", "kInitialCash"), ("max_position_weight", "kMaxPositionWeight"), ("commission_bps", "kCommissionBps"), ("slippage_bps", "kSlippageBps")):
        values.append(f"inline constexpr double {cpp_name} = {format(spec[field], '.17g')};")
    return "\n".join(values)


def preview_source(spec: Any, history_start: str = "2010-01-01") -> str:
    """Return the complete generated strategy and execution header."""
    cleaned = _plain_spec(spec)
    date.fromisoformat(history_start)
    return (_TEMPLATES / "strategy.hpp.in").read_text(encoding="utf-8").replace("// @GENERATED_CONFIGURATION@", _configuration(cleaned, history_start))


def _csv_data(dataset: Any) -> str | None:
    if dataset is None:
        return None
    if isinstance(dataset, bytes):
        return dataset.decode("utf-8-sig")
    if isinstance(dataset, str):
        return dataset
    if hasattr(dataset, "to_csv"):
        return dataset.to_csv()
    # The engine's Dataset exposes symbol -> ordered Bar objects.
    bars = getattr(dataset, "bars", None)
    if bars is None and isinstance(dataset, Mapping):
        bars = dataset.get("bars", dataset)
    rows: list[dict[str, Any]] = []
    if isinstance(bars, Mapping):
        for symbol, items in bars.items():
            for bar in items:
                row = dict(bar) if isinstance(bar, Mapping) else {key: getattr(bar, key, 0) for key in ("date", "open", "high", "low", "close", "volume")}
                row["symbol"] = symbol
                rows.append(row)
    elif isinstance(dataset, (list, tuple)):
        rows = [dict(row) for row in dataset]
    else:
        raise ValueError("Unsupported export dataset; provide daily CSV text.")
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["date", "symbol", "open", "high", "low", "close", "volume"], extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda row: (str(row["date"]), str(row["symbol"]))):
        writer.writerow(row)
    return out.getvalue()


def export_bundle(spec: Any, dataset: Any = None) -> bytes:
    """Build a deterministic ZIP with source, build instructions and optional bars."""
    cleaned = _plain_spec(spec)
    required = set(cleaned["symbols"] + [cleaned["benchmark"]])
    if isinstance(dataset, Mapping) and isinstance(dataset.get("bars"), Mapping):
        dataset = {**dataset, "bars": {symbol: bars for symbol, bars in dataset["bars"].items() if symbol in required}}
    csv_data = _csv_data(dataset)
    history_start = "2010-01-01"
    if csv_data:
        dates = [row.get("date", "") for row in csv.DictReader(io.StringIO(csv_data)) if row.get("symbol") in required]
        valid_dates = [day for day in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
        if valid_dates:
            history_start = min(valid_dates)
    files = {
        "strategy.hpp": (_TEMPLATES / "strategy.hpp.in").read_text(encoding="utf-8").replace("// @GENERATED_CONFIGURATION@", _configuration(cleaned, history_start)),
        "main.cpp": (_TEMPLATES / "main.cpp").read_text(encoding="utf-8"),
        "alpaca_paper.cpp": (_TEMPLATES / "alpaca_paper.cpp").read_text(encoding="utf-8"),
        "CMakeLists.txt": (_TEMPLATES / "CMakeLists.txt").read_text(encoding="utf-8"),
        "tests/core_tests.cpp": (_TEMPLATES / "core_tests.cpp").read_text(encoding="utf-8"),
        "README.md": (_TEMPLATES / "README.md.in").read_text(encoding="utf-8"),
        "strategy.json": json.dumps(cleaned, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        ".gitignore": "build/\npaper-state/\n.env\n",
    }
    if isinstance(dataset, Mapping) and "bars" in dataset:
        files["data_provenance.json"] = json.dumps({key: dataset.get(key) for key in ("source", "synthetic", "adjustment", "warnings")}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if csv_data is not None:
        files["bars.csv"] = csv_data
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, contents.encode("utf-8"))
    return buffer.getvalue()
