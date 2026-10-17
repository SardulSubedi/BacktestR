import copy
from datetime import date, datetime, timezone
import os
import unittest
from unittest.mock import patch

from backtestr.data import _completed_history_cutoff, demo_dataset, fetch_alpaca, parse_csv, validate_dataset


HEADER = "date,symbol,open,high,low,close,volume\n"
GOOD = "2024-01-02,AAPL,10,12,9,11,100\n"


class CsvTests(unittest.TestCase):
    def test_parses_sorts_and_normalizes(self):
        data = parse_csv(HEADER + "2024-01-03,aapl,11,12,9,10,0\n" + GOOD, "split")
        self.assertEqual([row["date"] for row in data["bars"]["AAPL"]], ["2024-01-02", "2024-01-03"])
        self.assertEqual(data["bars"]["AAPL"][0]["close"], 11)
        self.assertFalse(data["synthetic"])
        self.assertEqual(data["adjustment"], "split")

    def test_bom_is_accepted(self):
        self.assertIn("AAPL", parse_csv("\ufeff" + HEADER + GOOD)["bars"])

    def test_optional_synthetic_provenance_is_preserved(self):
        marked = HEADER.rstrip("\n") + ",synthetic\n" + GOOD.rstrip("\n") + ",true\n"
        data = parse_csv(marked, "split")
        self.assertTrue(data["synthetic"])
        self.assertIn("synthetic", data["source"])
        self.assertTrue(any("SYNTHETIC" in warning for warning in data["warnings"]))
        self.assertFalse(parse_csv(marked.replace(",true", ",false"))["synthetic"])

    def test_any_synthetic_row_marks_whole_mixed_dataset(self):
        text = HEADER.rstrip("\n") + ",synthetic\n" + GOOD.rstrip("\n") + ",false\n2024-01-03,AAPL,10,12,9,11,100,true\n"
        self.assertTrue(parse_csv(text)["synthetic"])

    def test_invalid_synthetic_markers_are_rejected(self):
        for marker in ("", "unknown", "1", "yes"):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "synthetic"):
                parse_csv(HEADER.rstrip("\n") + ",synthetic\n" + GOOD.rstrip("\n") + "," + marker + "\n")

    def test_rejects_duplicate_date(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_csv(HEADER + GOOD + GOOD)

    def test_invalid_data_is_rejected(self):
        bad = [
            "2024-02-30,AAPL,10,12,9,11,100\n",
            "20240102,AAPL,10,12,9,11,100\n",
            "2024-01-02,AAPL,nan,12,9,11,100\n",
            "2024-01-02,AAPL,10,inf,9,11,100\n",
            "2024-01-02,AAPL,10,9,9,11,100\n",
            "2024-01-02,AAPL,0,12,0,11,100\n",
            "2024-01-02,AAPL,10,12,9,11,-1\n",
            "2024-01-02,AAPL,10,12,9,11,1.5\n",
            "2024-01-02,AAPL,10,12,9,11\n",
            "2024-01-02,AAPL,10,12,9,11,100,extra\n",
            "2024-01-02,../secret,10,12,9,11,100\n",
        ]
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse_csv(HEADER + row)

    def test_missing_or_duplicate_columns_rejected(self):
        for text in ("", "date,symbol,close\n2024-01-02,AAPL,10", "date,date,symbol,open,high,low,close,volume\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_csv(text)

    def test_unknown_adjustment_is_disclosed(self):
        self.assertTrue(any("split adjusted" in warning for warning in parse_csv(HEADER + GOOD)["warnings"]))

    def test_validation_does_not_mutate_input(self):
        data = parse_csv(HEADER + GOOD)
        original = copy.deepcopy(data)
        normalized = validate_dataset(data)
        normalized["bars"]["AAPL"][0]["open"] = 99
        self.assertEqual(data, original)


class ProviderTests(unittest.TestCase):
    def test_current_session_is_not_mistaken_for_a_completed_daily_bar(self):
        self.assertEqual(_completed_history_cutoff(datetime(2026, 1, 8, 18, tzinfo=timezone.utc)), date(2026, 1, 7))
        self.assertEqual(_completed_history_cutoff(datetime(2026, 1, 9, 1, tzinfo=timezone.utc)), date(2026, 1, 7))
        self.assertEqual(_completed_history_cutoff(datetime(2026, 1, 9, 2, tzinfo=timezone.utc)), date(2026, 1, 8))

    def test_demo_is_deterministic_and_clearly_synthetic(self):
        first = demo_dataset()
        self.assertEqual(first, demo_dataset())
        self.assertTrue(first["synthetic"])
        self.assertTrue(any("SYNTHETIC" in warning for warning in first["warnings"]))
        self.assertGreater(len(first["bars"]["SPY"]), 3000)
        validate_dataset(first)

    def test_credentials_are_required_without_network_request(self):
        with patch.dict(os.environ, {}, clear=True), patch("backtestr.data.request.urlopen") as connection:
            with self.assertRaisesRegex(ValueError, "APCA_API_KEY_ID"):
                fetch_alpaca({})
            connection.assert_not_called()

    def test_provider_request_excludes_today_and_rejects_unfinished_response(self):
        import json

        class Response:
            def __init__(self, day):
                bar = {"t": day + "T05:00:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}
                self.raw = json.dumps({"bars": {"AAPL": [bar], "SPY": [bar]}, "next_page_token": None}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.raw[:limit]

        spec = {"symbols": ["AAPL"], "benchmark": "SPY", "start_date": "2026-01-01", "end_date": "2026-01-09"}
        environment = {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret", "ALPACA_DATA_FEED": "iex"}
        with patch.dict(os.environ, environment), patch("backtestr.data.datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 8, 18, tzinfo=timezone.utc)
            with patch("backtestr.data.request.urlopen", return_value=Response("2026-01-07")) as connection:
                data = fetch_alpaca(spec)
                self.assertIn("end=2026-01-07T23%3A59%3A59Z", connection.call_args[0][0].full_url)
                self.assertTrue(any("requested end_date 2026-01-09, effective provider end_date 2026-01-07" in warning for warning in data["warnings"]))
            for unfinished_date in ("2026-01-08", "2026-01-09"):
                with self.subTest(unfinished_date=unfinished_date), patch("backtestr.data.request.urlopen", return_value=Response(unfinished_date)), self.assertRaisesRegex(ValueError, "completed-history cutoff"):
                    fetch_alpaca(spec)

    def test_pagination_and_request_contract(self):
        import json

        class Response:
            def __init__(self, payload):
                self.raw = json.dumps(payload).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.raw[:limit]

        first = {"bars": {"AAPL": [{"t": "2024-01-02T05:00:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]}, "next_page_token": "next"}
        second = {"bars": {"SPY": [{"t": "2024-01-02T05:00:00Z", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}]}, "next_page_token": None}
        spec = {"symbols": ["AAPL"], "benchmark": "SPY", "start_date": "2024-01-04", "end_date": "2024-02-01"}
        with patch.dict(os.environ, {"APCA_API_KEY_ID": "test-key", "APCA_API_SECRET_KEY": "test-secret", "ALPACA_DATA_FEED": "iex"}), patch("backtestr.data.request.urlopen", side_effect=[Response(first), Response(second)]) as connection:
            data = fetch_alpaca(spec)
            self.assertEqual(connection.call_count, 2)
            url = connection.call_args[0][0].full_url
            self.assertIn("page_token=next", url)
            self.assertIn("adjustment=split", url)
            self.assertIn("end=2024-02-01T23%3A59%3A59Z", url)
            self.assertEqual(data["adjustment"], "split")
            self.assertNotIn("test-secret", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
