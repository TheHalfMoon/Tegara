"""Deterministic API tests; browser e2e is in e2e/ and runs separately."""
from datetime import date
import json
from http.server import ThreadingHTTPServer
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tegara.dashboard import calculate_payload, make_handler


def example():
    return {
        "symbol": "SPY",
        "right": "C",
        "strike": "600",
        "expiration": "2026-10-09",
        "entry_price": "1.20",
        "quantity": 1,
        "multiplier": 100,
        "entry_fee_per_contract": "2.29",
        "exit_fee_per_contract": "2.29",
        "tick": "0.05",
        "target_kind": "NET_RETURN_PERCENT",
        "target_value": "30",
    }


class TargetPayloadTests(unittest.TestCase):
    def test_net_return_calculation(self):
        x = calculate_payload(example())
        self.assertEqual(x["candidate_bid_target"], "1.65")
        self.assertEqual(x["estimated_net_pnl_usd"], "40.42")
        self.assertEqual(x["quote_source"], "NONE")

    def test_actual_contract_multiplier(self):
        body = example()
        body["multiplier"] = 10
        x = calculate_payload(body)
        self.assertEqual(x["initial_debit_usd"], "14.29")
        self.assertNotEqual(x["candidate_bid_target"], "1.65")

    def test_net_profit_target(self):
        body = example()
        body["target_kind"] = "NET_PROFIT_USD"
        body["target_value"] = "35"
        self.assertEqual(calculate_payload(body)["candidate_bid_target"], "1.60")

    def test_unknown_fields_fail_closed(self):
        body = example()
        body["broker_api_key"] = "must_not_be_accepted"
        with self.assertRaises(ValueError):
            calculate_payload(body)

    def test_all_invalid_money_rejected(self):
        for bad in ("NaN", "Infinity", "-1", "invalid"):
            with self.subTest(value=bad):
                body = example()
                body["entry_price"] = bad
                with self.assertRaises(ValueError):
                    calculate_payload(body)

    def test_zero_quantity_and_multiplier_rejected(self):
        for field in ("quantity", "multiplier"):
            body = example()
            body[field] = 0
            with self.assertRaises(ValueError):
                calculate_payload(body)

    def test_wrong_expiry_rejected(self):
        body = example()
        body["expiration"] = "not-a-date"
        with self.assertRaises(ValueError):
            calculate_payload(body)


class LoopbackWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_health_is_explicitly_research_only(self):
        with urlopen(self.url + "/api/health", timeout=3) as r:
            x = json.load(r)
        self.assertEqual(x["status"], "RESEARCH_ONLY")
        self.assertEqual(x["broker_orders"], "DISABLED")

    def test_html_has_real_form(self):
        with urlopen(self.url + "/", timeout=3) as r:
            text = r.read().decode()
        self.assertIn("Tegara", text)
        self.assertIn('id="target-form"', text)

    def test_real_api_post(self):
        req = Request(
            self.url + "/api/target",
            data=json.dumps(example()).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": self.url},
        )
        with urlopen(req, timeout=3) as response:
            self.assertEqual(json.load(response)["candidate_bid_target"], "1.65")

    def test_cors_rejects_cross_origin_post(self):
        req = Request(
            self.url + "/api/target",
            data=json.dumps(example()).encode(),
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 403)

    def test_wrong_host_rejected(self):
        req = Request(self.url + "/api/health", headers={"Host": "evil.example"})
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 403)

    def test_invalid_payload_is_not_accepted(self):
        req = Request(
            self.url + "/api/target",
            data=b"{broken",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 422)

    def test_no_broker_trade_api(self):
        req = Request(
            self.url + "/api/order",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as ctx:
            urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
