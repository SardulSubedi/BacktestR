"""Loopback-only application server. No credentials are returned to the browser."""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import threading
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .codegen import export_bundle, preview_source
from .data import demo_dataset, fetch_alpaca, parse_csv
from .engine import run_backtest
from .interpreter import analyze_strategy
from .strategy import default_spec, validate_spec

WEB = Path(__file__).parent / "web"
MAX_BODY = 20 * 1024 * 1024


class Application:
    def __init__(self):
        self.lock = threading.RLock()
        self.workers = threading.BoundedSemaphore(2)
        self.datasets = OrderedDict()
        self.datasets["demo"] = demo_dataset()

    def dataset(self, key):
        with self.lock:
            if key not in self.datasets:
                raise ValueError("This dataset is no longer loaded. Import the CSV again.")
            return self.datasets[key]

    def add_dataset(self, dataset):
        key = uuid.uuid4().hex
        with self.lock:
            self.datasets[key] = dataset
            while len(self.datasets) > 6:
                self.datasets.pop(next(k for k in self.datasets if k != "demo"))
        return self.describe(key)

    def describe(self, key):
        dataset = self.dataset(key)
        bars = dataset["bars"]
        dates = [b["date"] for rows in bars.values() for b in rows]
        return {"id": key, "source": dataset["source"],
                "synthetic": dataset["synthetic"], "adjustment": dataset["adjustment"],
                "symbols": sorted(bars), "start_date": min(dates), "end_date": max(dates),
                "rows": len(dates), "warnings": dataset.get("warnings", [])}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app=None):
        self.app = app or Application()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "BacktestR/0.1"

    def log_message(self, fmt, *args):
        # Request bodies, prompts, and credentials never enter logs.
        return

    def _allowed(self):
        host = self.headers.get("Host", "")
        expected_port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{expected_port}", f"localhost:{expected_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (not origin or origin in {f"http://{h}" for h in allowed})

    def _send(self, data, status=200, mime="application/json; charset=utf-8", filename=None):
        if mime.startswith("application/json"):
            data = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return self._send({"error": "Only requests from this local application are accepted."}, 403)
        path = urlsplit(self.path).path
        if path == "/api/config":
            return self._send({"version": "0.1.0", "ai_configured": bool(os.getenv("OPENAI_API_KEY")),
                               "ai_model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                               "alpaca_configured": bool(os.getenv("APCA_API_KEY_ID") and os.getenv("APCA_API_SECRET_KEY")),
                               "default_strategy": default_spec(),
                               "demo": self.server.app.describe("demo")})
        if path == "/api/health":
            return self._send({"ok": True})
        if path == "/api/sample.csv":
            dataset = self.server.app.dataset("demo")
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=["date", "symbol", "open", "high", "low", "close", "volume", "synthetic"])
            writer.writeheader()
            for symbol, bars in dataset["bars"].items():
                for bar in bars:
                    writer.writerow({**bar, "symbol": symbol, "synthetic": "true"})
            return self._send(output.getvalue(), mime="text/csv; charset=utf-8", filename="synthetic-demo-bars.csv")
        files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
        filename = files.get(path)
        if not filename or not (WEB / filename).is_file():
            return self._send({"error": "Page not found."}, 404)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return self._send((WEB / filename).read_bytes(), mime=mime + ("; charset=utf-8" if mime.startswith("text/") else ""))

    def do_POST(self):
        if not self._allowed():
            return self._send({"error": "Only requests from this local application are accepted."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self._send({"error": "Request must be JSON and smaller than 20 MB."}, 413)
            if self.headers.get_content_type() != "application/json":
                return self._send({"error": "Content-Type must be application/json."}, 415)
            body = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON numbers are not allowed.")))
            if not isinstance(body, dict):
                raise ValueError("Request body must be an object.")
            path = urlsplit(self.path).path
            if path == "/api/interpret":
                description = body.get("description", "")
                if not isinstance(description, str) or not 5 <= len(description.strip()) <= 12000:
                    raise ValueError("Describe your strategy in 5 to 12,000 characters.")
                answers = body.get("answers", {})
                if not isinstance(answers, dict):
                    raise ValueError("Clarification answers must be an object.")
                return self._send(analyze_strategy(description, answers, provider=body.get("provider", "auto")))
            if path == "/api/datasets":
                raw = body.get("csv")
                if not isinstance(raw, str) or len(raw) > 15 * 1024 * 1024:
                    raise ValueError("Choose a CSV file smaller than 15 MB.")
                return self._send(self.server.app.add_dataset(parse_csv(raw, adjustment=body.get("adjustment", "unknown"))))
            if path in {"/api/backtest", "/api/export", "/api/data/alpaca", "/api/preview"}:
                spec = validate_spec(body.get("strategy", {}))
                if path == "/api/data/alpaca":
                    return self._send(self.server.app.add_dataset(fetch_alpaca(spec)))
                if path == "/api/preview":
                    history_start = "2010-01-01"
                    if body.get("dataset_id"):
                        selected = self.server.app.dataset(body["dataset_id"])
                        required = set(spec["symbols"] + [spec["benchmark"]])
                        dates = [bar["date"] for symbol, bars in selected["bars"].items() if symbol in required for bar in bars]
                        if dates:
                            history_start = min(dates)
                    return self._send({"source": preview_source(spec, history_start)})
                dataset = self.server.app.dataset(body.get("dataset_id", "demo"))
                if path == "/api/export":
                    # Validate coverage/timing before producing a runnable replay bundle.
                    run_backtest(spec, dataset)
                    return self._send(export_bundle(spec, dataset), mime="application/zip", filename="backtestr-cpp.zip")
                if not self.server.app.workers.acquire(blocking=False):
                    return self._send({"error": "Two studies are already running. Try again shortly."}, 429)
                try:
                    result = run_backtest(spec, dataset)
                    result["strategy"] = spec
                    result["dataset"] = self.server.app.describe(body.get("dataset_id", "demo"))
                    return self._send(result)
                finally:
                    self.server.app.workers.release()
            return self._send({"error": "Endpoint not found."}, 404)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return self._send({"error": str(exc)[:600]}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return self._send({"error": "The operation failed. Check the data and local configuration, then retry."}, 500)


def serve(port=8765):
    server = Server(("127.0.0.1", port))
    print(f"BacktestR is running at http://127.0.0.1:{port}", flush=True)
    print("Press Ctrl+C to stop. Credentials stay on this computer.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
