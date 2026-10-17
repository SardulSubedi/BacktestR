# BacktestR

A local research application that turns an English stock-trading idea into explicit rules, clarifies missing decisions, builds the portfolio, tests it across periods, and exports a buildable C++20 project.

## Run it

Requires Python 3.11 or later. The research application has **no third-party Python dependencies**.

```powershell
git clone https://github.com/SardulSubedi/BacktestR.git
cd BacktestR
python -m backtestr
```

Open **http://127.0.0.1:8765**. On systems where Python is named `python3`, use that command instead. Use `python -m backtestr serve --port 8766` to choose another port.

1. Describe the strategy or select an example.
2. Answer material clarification questions.
3. Review the complete specification and disclosed assumptions. It can be edited or saved as JSON.
4. Select synthetic demo data, import a CSV, or fetch Alpaca history.
5. Run the study; inspect performance, holdout, cost stress, independent periods, and every fill.
6. Download the report or the C++ project with its strategy and data provenance.

The demo prices are deterministic **synthetic prices**, including synthetic weekday sessions. They do not represent the actual performance of the ticker labels. No profitability claim is made.

## Enable AI and market data

Copy `.env.example` to `.env`, set the values you need, and restart the server. Existing process environment variables take precedence. `.env` is ignored by Git. Credentials are read only by the local backend.

```dotenv
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1-mini
APCA_API_KEY_ID=your-alpaca-key
APCA_API_SECRET_KEY=your-alpaca-secret
ALPACA_DATA_FEED=iex
```

With an OpenAI key, Automatic mode uses the Responses API with strict structured output and `store: false`. The model translates English into a constrained specification; it never executes arbitrary generated Python or C++. A refusal, invalid strategy, unsupported rule, or API error is shown explicitly. A configured AI failure does not silently fall back to another interpretation. Provider calls may incur charges. Descriptions and clarification answers are sent to OpenAI when AI interpretation is selected.

Without a key, a **local rule parser, explicitly labeled as not AI**, supports precise descriptions of the four implemented rule families. It deliberately rejects unfamiliar extra clauses. Broad ideas often need the AI interpreter or a complete JSON specification. The current model setting is configurable; availability depends on your account.

Alpaca history uses daily, split-adjusted bars, pagination, warmup, finite request/row limits, and an explicit feed. IEX is a single-exchange feed; SIP requires the appropriate data entitlement. The browser application does not submit broker orders.

## Supported strategy language

- **SMA trend:** hold a symbol when its fast closing-price SMA is strictly above its slow SMA.
- **RSI mean reversion:** enter at or below an entry threshold and exit at or above an exit threshold. Uses simple rolling mean gains/losses, **not Wilder smoothing**. Flat windows have RSI 50.
- **Momentum:** hold when the trailing `lookback`-session price return is greater than `entry_threshold`. Thresholds are fractions: `0.05` means 5%.
- **Buy and hold:** every selected symbol is active; the selected rebalance cadence resets its target allocation.

The universe is explicit. Active symbols share equal weights subject to a position cap; the remainder stays in USD cash. Long-only, whole shares, no leverage. Rebalance daily, or on the first available session of the new ISO week/month. No stop losses, intraday triggers, shorting, derivatives, custom unequal weights, arbitrary factor logic, or automatic parameter optimization are implemented. Unsupported rules are rejected rather than silently approximated. A complete technical description needs no extra material questions, but all execution conventions still appear in the review.

Example:

> Trade AAPL and MSFT with a 20/100 SMA crossover. Initial capital $100,000, maximum 50% per stock. Rebalance daily from 2020-01-01 to 2025-12-31. Benchmark SPY, commission 1 bps and slippage 5 bps.

The full machine-readable example is in `examples/sma_trend.json`.

## Backtesting conventions and limits

Completed close **t** produces a signal that can first fill at open **t+1**. The first test close is an initial-cash baseline. Warmup bars precede the selected start. The benchmark buys whole shares at the same first execution open with the same costs. Final positions remain open and are valued at the final close; final liquidation costs are not charged.

At a scheduled open, targets use opening marked equity and prices. Sales precede buys. Fixed basis-point slippage and commission apply to each fill. Cash constraints can reduce buys; alphabetical execution order can favor earlier symbols. Position caps apply to targets at rebalance opens and can drift afterward.

The report calculates total return, calendar-time CAGR, annualized volatility, Sharpe at a zero risk-free rate, maximum drawdown, benchmark/excess return, costs, holdings, and a daily equity series. It also runs:

- Chronological 70/30 development/holdout evaluations with unchanged parameters.
- Independent annual and overlapping 252-return-observation periods, each starting with fresh cash and prior indicator history.
- Two- and three-times transaction-cost stress.
- A fixed-seed block bootstrap of observed returns, with a conditional interval rather than a forecast.

This is rigorous software testing and fixed-rule historical diagnosis, not proof that a strategy will work. Repeatedly adjusting a strategy after viewing the holdout compromises it. The simulator does not model market impact, volume capacity, bid/ask spreads, partial fills, queue position, halts, taxes, interest, or a point-in-time security universe. Fixed current-stock lists can introduce survivorship bias. Split-only returns exclude dividends; dividend-adjusted OHLC embeds an approximate reinvestment effect and changes execution-price interpretation. Corporate-action and data-quality verification remain necessary.

## CSV format

Use a long-format CSV with matching sessions for every strategy symbol and the benchmark, including enough earlier history for indicator warmup:

```csv
date,symbol,open,high,low,close,volume
2020-01-02,AAPL,75.1,75.2,74.5,75.0,1000000
2020-01-02,MSFT,158.0,160.0,157.0,159.0,2000000
2020-01-02,SPY,322.0,324.0,321.0,323.0,3000000
```

These rows are format examples, not sourced historical prices. Dates must be ISO `YYYY-MM-DD`; OHLC values must use the same adjustment basis and pass finite/positive/range consistency checks. Duplicate symbol/date rows fail. Missing sessions are not forward-filled. Up to 100,000 total bars and a 15 MB browser upload are supported. Select the actual price basis on import. An optional `synthetic` column accepts `true`/`false`; any true row marks the entire dataset synthetic. Downloaded demo CSVs include that marker so reimporting them retains the label. Arbitrary imported data cannot be authenticated as historical by the app.

## Generated C++ project

The ZIP contains `strategy.hpp`, `main.cpp`, `alpaca_paper.cpp`, `CMakeLists.txt`, core tests, the validated strategy JSON, supplied bars, provenance, and a deployment README. Generation uses deterministic templates and validated parameters, with no model call in the trading loop.

Build replay with CMake 3.20+ and a C++20 compiler:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
./build/backtestr_replay bars.csv --trades trades.csv
```

On a multi-configuration Windows generator, use `build/Release/backtestr_replay.exe`.

The **optional Alpaca paper executor** requires libcurl and nlohmann/json. Build with `-DBACKTESTR_ALPACA_PAPER=ON`. Without `--submit-paper`, it previews orders. With that explicit flag, it submits limit-on-open orders to the fixed Alpaca **paper** endpoint. It checks account state, holdings, tradable assets, completed-session history, schedule, cash, open orders, deterministic order IDs, and a persistent per-session journal. Capital is capped by the research allocation and broker equity; buys reserve cash and do not anticipate proceeds from pending sells. It stops on uncertain/partial failures for reconciliation. Use a dedicated paper account and read the generated README.

Paper orders intentionally differ from simulated next-open market fills: limit prices, gaps, rejected/unfilled auctions, and cash reservations can produce different portfolios. Historical start/end dates bound replay; paper execution uses current completed-session signals. State and price-history basis must match the study. The executable runs once per scheduled session; operate it with an external scheduler and monitoring. Live-money endpoints, unattended recovery, fill reconciliation/replanning, and high-frequency latency guarantees are not implemented. This is a runnable research and paper-deployment foundation, not a production live-trading service.

## CLI and development

```sh
python -m backtestr interpret "Use a simple moving average strategy" --provider local
python -m backtestr backtest --strategy examples/sma_trend.json --out report.json
python -m backtestr export --strategy examples/sma_trend.json --out strategy.zip
python -m backtestr backtest --strategy examples/sma_trend.json --data prices.csv --adjustment split --out report.json
python -m unittest discover -s tests -v
```

The app runs directly from a checkout. Optional package installation: `python -m pip install .`. Generated templates and browser assets ship in the package. Draft descriptions are saved in this browser’s local storage; imported datasets are in server memory and disappear when it restarts. Use Save JSON and report downloads to preserve work.

The HTTP server binds only to loopback, checks Host and Origin, limits payload size/concurrent studies, and serves only allowlisted static files. It is a local single-user development server; do not expose it publicly. Tests exercise causality, numerical edge cases, input rejection, accounting, provider pagination/errors, clarification, schema/refusal handling, HTTP boundaries, and Python/C++ result parity when a compiler exists. CI additionally builds the optional broker adapter without sending orders.

Official integration references: [OpenAI structured output](https://developers.openai.com/api/docs/guides/structured-outputs), [Alpaca historical bars](https://docs.alpaca.markets/us/reference/stockbars), [Alpaca paper trading](https://docs.alpaca.markets/us/docs/paper-trading).
