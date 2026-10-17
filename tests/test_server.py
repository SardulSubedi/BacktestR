"""Exercise actual HTTP boundaries and the complete local workflow."""

import csv
import http.client
import io
import json
import threading
import unittest
import zipfile

from backtestr.data import parse_csv
from backtestr.server import Server
from backtestr.strategy import default_spec


class ApplicationHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(("127.0.0.1", 0))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request("POST" if data is not None else "GET", path, body=data, headers=request_headers)
        response = connection.getresponse()
        result = response.read()
        status, mime = response.status, response.getheader("Content-Type")
        connection.close()
        return status, json.loads(result) if mime.startswith("application/json") else result

    def test_host_and_origin_reject_cross_site_requests(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}, {"Origin": "http://127.0.0.1:1"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/config", headers=headers)[0], 403)

    def test_static_allowlist_and_config(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Research report", body)
        self.assertEqual(self.request("/../.env")[0], 404)
        self.assertEqual(self.request("/.git/config")[0], 404)
        status, config = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertIs(type(config["ai_configured"]), bool)
        self.assertTrue(config["demo"]["synthetic"])
        self.assertNotIn("OPENAI_API_KEY", json.dumps(config))
        self.assertNotIn("APCA_API_SECRET_KEY", json.dumps(config))

    def test_nonfinite_and_wrong_json_shapes_are_rejected(self):
        for raw in (b'{"strategy":{"initial_cash":NaN}}', b'[]', b'null', b'{broken'):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("/api/backtest", raw=raw)[0], 400)

    def test_interpretation_and_invalid_description(self):
        self.assertEqual(self.request("/api/interpret", {"description": "hi", "provider": "local"})[0], 400)
        description = "Trade AAPL and MSFT with a 20/100 SMA crossover. Initial capital $100,000, maximum 50% per stock. Rebalance daily from 2020-01-01 to 2025-12-31."
        status, result = self.request("/api/interpret", {"description": description, "provider": "local"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["assumptions"])

    def test_sample_csv_reimport_preserves_synthetic_provenance(self):
        status, raw = self.request("/api/sample.csv")
        self.assertEqual(status, 200)
        self.assertTrue(parse_csv(raw.decode())["synthetic"])
        self.assertIn("synthetic", next(csv.reader(io.StringIO(raw.decode()))))

    def test_backtest_returns_actual_equity_and_robustness(self):
        status, result = self.request("/api/backtest", {"strategy": default_spec(), "dataset_id": "demo"})
        self.assertEqual(status, 200)
        self.assertEqual(result["equity"][0]["equity"], 100000)
        self.assertGreater(len(result["equity"]), 1000)
        self.assertEqual(result["metrics"]["final_equity"], result["equity"][-1]["equity"])
        self.assertTrue(result["dataset"]["synthetic"])
        self.assertIsNotNone(result["robustness"]["split"]["holdout"])
        self.assertEqual(len(result["robustness"]["cost_stress"]), 2)

    def test_unknown_rule_and_dataset_fail_before_execution(self):
        status, _ = self.request("/api/backtest", {"strategy": {**default_spec(), "stop_loss": 0.05}})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/backtest", {"strategy": default_spec(), "dataset_id": "expired"})[0], 400)

    def test_export_is_concrete_buildable_bundle(self):
        status, raw = self.request("/api/export", {"strategy": default_spec(), "dataset_id": "demo"})
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIn("main.cpp", archive.namelist())
            self.assertIn("bars.csv", archive.namelist())
            self.assertTrue(json.loads(archive.read("data_provenance.json"))["synthetic"])
            self.assertEqual(json.loads(archive.read("strategy.json"))["symbols"], ["AAPL", "MSFT"])
            status, preview = self.request("/api/preview", {"strategy": default_spec(), "dataset_id": "demo"})
            self.assertEqual(status, 200)
            self.assertEqual(preview["source"], archive.read("strategy.hpp").decode())


if __name__ == "__main__":
    unittest.main()
