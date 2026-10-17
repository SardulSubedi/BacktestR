# Validation of the initial implementation

Local verification on Windows with Python 3.12 and Node 24:

- Python/HTTP suite: 81 discovered tests, 78 passed and 3 compiler-dependent tests skipped in the ordinary Python pass.
- Separate compiled export pass: all 8 export tests passed, including those 3 compiled tests. A pinned official Zig 0.14.1 portable compiler was downloaded, SHA-256 checked, and used only from the ignored `.tools` directory.
- Compiled tests compare all four supported signal families against the Python engine, exercise next-open execution and costs, reject mismatched calendars, and guard against false SMA signals on flat decimal prices.
- Browser workflow verified: complete technical description, missing-information questions and answer application, synthetic study, holdout/stress/bootstrap views, and generated C++ preview. Desktop and 390px layouts were inspected. Responsive charts use actual container dimensions; hiding a tab cannot overwrite its scale.
- JavaScript syntax check passed. An isolated package build/install confirmed that the browser files and C++ templates ship in the installed package.

No OpenAI API key or Alpaca credentials were supplied. Actual provider requests and broker submissions were not exercised; strict API payloads, pagination, refusals, errors, and unfinished-bar rejection use mocked tests. No live-money endpoint is included.

The optional Alpaca adapter is built by the included Ubuntu CI workflow with libcurl and nlohmann/json. Local replay compilation does not establish broker correctness or execution quality. Historical results, synthetic diagnostics, a successful build, and paper fills cannot establish future profitability or high-frequency latency.
