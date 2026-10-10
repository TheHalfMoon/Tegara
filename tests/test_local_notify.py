"""No-real-trade tests for local macOS notification demonstration."""
from datetime import date
from decimal import Decimal as D
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import TestCase
from unittest.mock import patch

from tegara import OptionContract, LongOptionPosition
from tegara.local_notify import (
    ResearchNotice, make_manual_demo_notice, show_macos_demo, SCRIPT
)


class LocalNotificationTests(TestCase):
    def setUp(self):
        contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        self.pos = LongOptionPosition(contract, D("1.20"), 1, D("2"), D("2"))

    def notice(self, bid="2.00", profit="30"):
        return make_manual_demo_notice(
            self.pos, manual_bid=D(bid), target_net_profit=D(profit)
        )

    def test_manual_achieved_bid_is_never_a_live_quote(self):
        n = self.notice()
        self.assertIn("ABOVE", n.body)
        self.assertIn("CHECK DERAYAH; NO ORDER PLACED", n.body)
        self.assertEqual(n.status, "LOCAL_MANUAL_RESEARCH_DEMO")
        self.assertFalse(n.market_data_verified)
        self.assertFalse(n.phone_delivery_verified)
        self.assertFalse(n.broker_execution_verified)

    def test_below_target_is_clear(self):
        n = self.notice(bid="1.21")
        self.assertIn("BELOW", n.body)
        self.assertIn("MANUAL BID / NOT VERIFIED", n.body)

    def test_negative_and_nonfinite_bid_rejected(self):
        for bid in ("-1", "NaN", "Infinity"):
            with self.subTest(bid=bid):
                with self.assertRaises(ValueError):
                    self.notice(bid=bid)

    def test_target_invalid_rejected(self):
        for target in ("-1", "NaN", "Infinity"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    self.notice(profit=target)

    def test_extreme_finite_bid_and_goal_cannot_create_notification(self):
        for value in ("1e999999999", "1e-999999999", "0e999999999"):
            for name in ("bid", "profit"):
                with self.subTest(value=value, field=name):
                    with self.assertRaisesRegex(ValueError, "bounded"):
                        self.notice(**{name: value})

    def test_must_not_create_unlabelled_notification(self):
        with self.assertRaisesRegex(ValueError, "unlabelled"):
            ResearchNotice("Sell your CALL NOW!", "MANUAL BID / NOT VERIFIED")
        with self.assertRaisesRegex(ValueError, "must state manual"):
            ResearchNotice("Tegara RESEARCH DEMO", "Verified live quote")

    def test_verified_broker_or_phone_claim_cannot_be_injected(self):
        safe = self.notice()
        for kw in (
            {"status": "LIVE_SELL_NOW"},
            {"market_data_verified": True},
            {"phone_delivery_verified": True},
            {"broker_execution_verified": True},
        ):
            with self.subTest(kw=kw):
                with self.assertRaisesRegex(ValueError, "cannot represent verified"):
                    ResearchNotice(safe.title, safe.body, **kw)

    def test_send_requires_explicit_authorization(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            show_macos_demo(self.notice())

    def test_send_is_macos_only_and_uses_fixed_script_argv(self):
        with patch("tegara.local_notify.sys.platform", "darwin"), \
             patch("tegara.local_notify.subprocess.run") as proc:
            proc.return_value.returncode = 0
            self.assertTrue(show_macos_demo(self.notice(), explicitly_authorized=True))
            args = proc.call_args.args[0]
            self.assertEqual(args[0], "osascript")
            self.assertEqual(args.count("-e"), len(SCRIPT))
            self.assertEqual(args[-3], "--")
            self.assertEqual(args[-2], self.notice().title)
            self.assertEqual(args[-1], self.notice().body)
            self.assertNotIn("shell", proc.call_args.kwargs)
            self.assertEqual(proc.call_args.kwargs["timeout"], 8)

    def test_nonmac_platform_fails(self):
        with patch("tegara.local_notify.sys.platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "only macOS"):
                show_macos_demo(self.notice(), explicitly_authorized=True)

    def test_nonzero_transport_exit_never_claims_success(self):
        with patch("tegara.local_notify.sys.platform", "darwin"), \
             patch("tegara.local_notify.subprocess.run") as proc:
            proc.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, "script failed"):
                show_macos_demo(self.notice(), explicitly_authorized=True)

    def run_cli(self, *args):
        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, "-m", "tegara.local_notify_cli",
             "--symbol", "SPY", "--right", "C", "--strike", "600",
             "--expiration", "2026-10-09", "--entry", "1.20",
             "--quantity", "1", "--entry-fee", "2", "--exit-fee", "2",
             "--net-profit", "30", "--manual-bid", "2.0", *args],
            cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")},
            capture_output=True, text=True,
        )

    def test_preview_does_not_call_os_notification(self):
        p = self.run_cli("--preview")
        self.assertEqual(p.returncode, 0, p.stderr)
        value = json.loads(p.stdout)
        self.assertFalse(value["local_os_request_accepted"])
        self.assertFalse(value["phone_delivery_verified"])

    def test_cli_send_flag_needs_ack(self):
        p = self.run_cli("--send-local")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--acknowledge-demo-only", p.stderr)

    def test_cli_rejects_unsafe_numeric(self):
        root = Path(__file__).resolve().parents[1]
        p = subprocess.run(
            [sys.executable, "-m", "tegara.local_notify_cli", "--help"],
            cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")},
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
