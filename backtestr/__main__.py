"""Run with python -m backtestr; the research core needs no installed packages."""

import argparse
import json
from pathlib import Path

from .config import load_env


def main():
    load_env()
    parser = argparse.ArgumentParser(prog="backtestr", description="English-to-strategy daily-stock research workspace")
    sub = parser.add_subparsers(dest="command")
    web = sub.add_parser("serve", help="Start the local browser application")
    web.add_argument("--port", type=int, default=8765)
    interpret = sub.add_parser("interpret", help="Interpret an English description")
    interpret.add_argument("description")
    interpret.add_argument("--provider", choices=["auto", "local", "openai"], default="auto")
    for name in ("backtest", "export"):
        command = sub.add_parser(name)
        command.add_argument("--strategy", type=Path, required=True)
        command.add_argument("--data", type=Path, help="Long-format daily OHLCV CSV; omitted uses synthetic demo")
        command.add_argument("--adjustment", default="unknown")
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command in (None, "serve"):
        from .server import serve
        port = getattr(args, "port", 8765)
        if not 1024 <= port <= 65535:
            parser.error("Port must be between 1024 and 65535.")
        serve(port)
        return
    try:
        if args.command == "interpret":
            from .interpreter import analyze_strategy
            print(json.dumps(analyze_strategy(args.description, provider=args.provider), indent=2, allow_nan=False))
            return
        from .strategy import validate_spec
        from .data import demo_dataset, parse_csv
        from .engine import run_backtest
        spec = validate_spec(json.loads(args.strategy.read_text(encoding="utf-8-sig")))
        dataset = parse_csv(args.data.read_text(encoding="utf-8-sig"), args.adjustment) if args.data else demo_dataset()
        report = run_backtest(spec, dataset)
        if args.command == "backtest":
            args.out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        else:
            from .codegen import export_bundle
            args.out.write_bytes(export_bundle(spec, dataset))
        print(f"Saved {args.out.resolve()}")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"BacktestR: {exc}\n")


if __name__ == "__main__":
    main()
