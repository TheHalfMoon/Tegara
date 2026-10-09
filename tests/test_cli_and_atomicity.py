import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile

from tegara import LongOptionPosition, OptionContract, OptionQuote
from tegara.position_store import MonitorDecision, PositionStore


class TargetCLITests(unittest.TestCase):
    def call(self, *extras):
        return subprocess.run(
            [sys.executable, "-m", "tegara", "target",
             "--symbol", "SPY", "--right", "C", "--strike", "600",
             "--expiration", "2026-10-09", "--entry", "1.20",
             "--quantity", "1", "--entry-fee", "2.29",
             "--exit-fee", "2.29", "--tick", "0.05", *extras],
            capture_output=True, text=True, env=os.environ,
        )

    def test_rounds_up_to_explicit_tick(self):
        import json
        p = self.call("--net-return", "30")
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["rounded_target_bid"], "1.65")
        self.assertEqual(out["estimated_net_pnl_at_target_usd"], "40.42")
        self.assertEqual(out["status"], "HYPOTHETICAL_TARGET_ONLY")

    def test_requires_user_supplied_fees(self):
        p = subprocess.run(
            [sys.executable, "-m", "tegara", "target", "--symbol", "SPY",
             "--right", "C", "--strike", "600", "--expiration", "2026-10-09",
             "--entry", "1.20", "--quantity", "1", "--entry-fee", "2.29",
             "--net-profit", "35", "--tick", "0.05"],
            capture_output=True, text=True, env=os.environ,
        )
        self.assertNotEqual(p.returncode, 0)

    def test_nan_rejected(self):
        self.assertNotEqual(self.call("--net-profit", "NaN").returncode, 0)

    def test_zero_tick_rejected(self):
        p = self.call("--net-profit", "35", "--tick", "0")
        self.assertNotEqual(p.returncode, 0)

    def test_multiple_targets_rejected(self):
        self.assertNotEqual(self.call("--net-profit", "35", "--net-return", "30").returncode, 0)


class AtomicLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        self.at = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
        self.contract = OptionContract("SPY", "C", D("600"), date(2026, 10, 9))
        with PositionStore(self.path) as store:
            store.open("p", LongOptionPosition(self.contract, D("1.20"), 3,
                                               D("2.29"), D("2.29")),
                       self.at - timedelta(minutes=2))

    def test_same_quote_from_two_workers_generates_only_one_alert(self):
        quote = OptionQuote(self.contract, D("1.62"), D("1.66"), 5,
                            self.at, "test", "EXACT_NBBO")
        def worker(_):
            with PositionStore(self.path) as store:
                return store.on_quote("p", quote, self.at,
                                      timedelta(seconds=3), D("105")).decision
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(results.count(MonitorDecision.ALERT_QUEUED), 1)
        self.assertEqual(results.count(MonitorDecision.OUT_OF_ORDER), 1)
        with PositionStore(self.path) as store:
            self.assertEqual(len(store.pending("p")), 1)

    def test_two_concurrent_closes_cannot_oversell(self):
        def worker(i):
            with PositionStore(self.path) as store:
                try:
                    return store.confirm_manual_close(
                        "p", f"fill-{i}", self.contract, 2,
                        D("1.62"), D("2.29"), self.at
                    )[2]
                except ValueError:
                    return "REJECTED"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count("REJECTED"), 1)
        with PositionStore(self.path) as store:
            self.assertEqual(store.get("p").open_quantity, 1)


if __name__ == "__main__":
    unittest.main()
