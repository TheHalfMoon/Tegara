"""Local options position ledger and replay-safe exit alert outbox.

Research-only. Neither sending alerts nor placing orders nor confirming fills
can occur implicitly. A human records any Derayah sell-to-close execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
import os
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from .core import (
    AlertStatus, LongOptionPosition, OptionContract, OptionQuote, assess_profit_target
)


def stamp(t: datetime) -> str:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return t.astimezone(timezone.utc).isoformat(timespec="microseconds")


def money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("amount must be a finite nonnegative Decimal")
    return value


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"


class MonitorDecision(str, Enum):
    ALERT_QUEUED = "ALERT_QUEUED"
    TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
    ALREADY_NOTIFIED = "ALREADY_NOTIFIED"
    CLOSED = "CLOSED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    UNQUALIFIED_FEED = "UNQUALIFIED_FEED"
    STALE_QUOTE = "STALE_QUOTE"
    INSUFFICIENT_SIZE = "INSUFFICIENT_SIZE"
    EXPIRED = "EXPIRED"
    CUTOFF_UNVERIFIED = "CUTOFF_UNVERIFIED"
    AFTER_CUTOFF = "AFTER_CUTOFF"
    WRONG_TRADING_DATE = "WRONG_TRADING_DATE"


@dataclass(frozen=True)
class Snapshot:
    position_id: str
    contract: OptionContract
    entry_price: Decimal
    initial_quantity: int
    open_quantity: int
    entry_fee: Decimal
    exit_fee: Decimal
    opened_at: datetime
    realized_net_pnl: Decimal

    @property
    def status(self) -> PositionStatus:
        if self.open_quantity == 0:
            return PositionStatus.CLOSED
        if self.open_quantity < self.initial_quantity:
            return PositionStatus.PARTIALLY_CLOSED
        return PositionStatus.OPEN

    def remaining(self) -> LongOptionPosition:
        if self.open_quantity == 0:
            raise ValueError("position is closed")
        return LongOptionPosition(self.contract, self.entry_price,
                                  self.open_quantity, self.entry_fee, self.exit_fee)


@dataclass(frozen=True)
class MonitorResult:
    decision: MonitorDecision
    notification_id: str | None = None
    net_pnl_estimate: Decimal | None = None


class PositionStore:
    """One local SQLite connection. Callers must serialize writes for now."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        location = str(path)
        if location != ":memory:":
            p = Path(location)
            if p.is_symlink():
                raise ValueError("SQLite position database symlinks are refused")
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.is_symlink():
                raise ValueError("SQLite position database symlinks are refused")
            if p.exists() and not p.is_file():
                raise ValueError("SQLite position database must be a regular file")
            if p.exists():
                os.chmod(location, 0o600)
        self.db = sqlite3.connect(location, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if location != ":memory:":
            os.chmod(location, 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
              id TEXT PRIMARY KEY, symbol TEXT NOT NULL, right_type TEXT NOT NULL,
              strike TEXT NOT NULL, expiry TEXT NOT NULL, multiplier INTEGER NOT NULL,
              entry_price TEXT NOT NULL, initial_qty INTEGER NOT NULL,
              open_qty INTEGER NOT NULL, entry_fee TEXT NOT NULL,
              exit_fee TEXT NOT NULL, opened_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills (
              position_id TEXT NOT NULL REFERENCES positions(id),
              fill_id TEXT NOT NULL, qty INTEGER NOT NULL, price TEXT NOT NULL,
              exit_fee TEXT NOT NULL, at TEXT NOT NULL, net TEXT NOT NULL,
              PRIMARY KEY(position_id, fill_id)
            );
            CREATE TABLE IF NOT EXISTS monitor (
              position_id TEXT PRIMARY KEY REFERENCES positions(id),
              armed INTEGER NOT NULL DEFAULT 1, last_quote_at TEXT,
              last_target TEXT
            );
            CREATE TABLE IF NOT EXISTS outbox (
              id TEXT PRIMARY KEY, position_id TEXT NOT NULL REFERENCES positions(id),
              quote_at TEXT NOT NULL, bid TEXT NOT NULL,
              net TEXT NOT NULL, target TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT NOT NULL,
              quote_expires_at TEXT, contract_cutoff_at TEXT,
              claim_token TEXT, claimed_at TEXT, accepted_at TEXT,
              receipt_digest TEXT
            );
            CREATE TABLE IF NOT EXISTS provider_ack_events (
              notice_id TEXT PRIMARY KEY REFERENCES outbox(id),
              accepted_at TEXT NOT NULL,
              receipt_digest TEXT NOT NULL,
              considered_timely INTEGER NOT NULL
            );
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(monitor)")}
        if "last_target" not in columns:
            self.db.execute("ALTER TABLE monitor ADD COLUMN last_target TEXT")
        outbox_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(outbox)")
        }
        # Old development-database pending rows have no immutable policy.
        # They must fail closed at revalidation; never infer one on migration.
        for field in (
            "quote_expires_at", "contract_cutoff_at", "claim_token",
            "claimed_at", "accepted_at", "receipt_digest",
        ):
            if field not in outbox_columns:
                self.db.execute(f"ALTER TABLE outbox ADD COLUMN {field} TEXT")

    @contextmanager
    def _transaction(self):
        # Explicit lock before any read: sqlite autocommit would otherwise
        # permit duplicate alerts or double-selling across processes.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "PositionStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self, key: str, position: LongOptionPosition, at: datetime) -> Snapshot:
        if not key or len(key) > 128:
            raise ValueError("position key must contain 1–128 characters")
        c = position.contract
        with self._transaction():
            self.db.execute(
                "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, c.symbol, c.right, str(c.strike), c.expiration.isoformat(),
                 c.multiplier, str(position.entry_price), position.quantity,
                 position.quantity, str(position.entry_fee_per_contract),
                 str(position.exit_fee_per_contract), stamp(at)),
            )
            self.db.execute("INSERT INTO monitor(position_id) VALUES (?)", (key,))
        return self.get(key)

    def get(self, key: str) -> Snapshot:
        r = self.db.execute("SELECT * FROM positions WHERE id=?", (key,)).fetchone()
        if r is None:
            raise KeyError(key)
        rows = self.db.execute("SELECT net FROM fills WHERE position_id=?", (key,))
        net = sum((Decimal(x["net"]) for x in rows), Decimal(0))
        contract = OptionContract(r["symbol"], r["right_type"], Decimal(r["strike"]),
                                  date.fromisoformat(r["expiry"]), r["multiplier"])
        return Snapshot(key, contract, Decimal(r["entry_price"]), r["initial_qty"],
                        r["open_qty"], Decimal(r["entry_fee"]), Decimal(r["exit_fee"]),
                        datetime.fromisoformat(r["opened_at"]), net)

    def confirm_manual_close(
        self, key: str, fill_id: str, contract: OptionContract, quantity: int,
        fill_price: Decimal, fee_per_contract: Decimal, filled_at: datetime
    ) -> tuple[Snapshot, Decimal, bool]:
        """Returns (position, fill net P&L, newly recorded). Never called by alerts."""
        if not fill_id or len(fill_id) > 128:
            raise ValueError("fill_id must contain 1–128 characters")
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        money(fill_price)
        money(fee_per_contract)
        ts = stamp(filled_at)
        with self._transaction():
            snap = self.get(key)
            if snap.contract != contract:
                raise ValueError("contract identity mismatch")
            if ts < stamp(snap.opened_at):
                raise ValueError("close fill precedes entry")
            prior = self.db.execute(
                "SELECT * FROM fills WHERE position_id=? AND fill_id=?", (key, fill_id)
            ).fetchone()
            if prior is not None:
                if (prior["qty"], prior["price"], prior["exit_fee"], prior["at"]) != (
                    quantity, str(fill_price), str(fee_per_contract), ts
                ):
                    raise ValueError("conflicting duplicate fill identifier")
                return snap, Decimal(prior["net"]), False
            if quantity > snap.open_quantity:
                raise ValueError("cannot sell more than current open quantity")
            net = ((fill_price - snap.entry_price) * snap.contract.multiplier
                   - snap.entry_fee - fee_per_contract) * quantity
            self.db.execute("INSERT INTO fills VALUES (?,?,?,?,?,?,?)",
                            (key, fill_id, quantity, str(fill_price), str(fee_per_contract),
                             ts, str(net)))
            self.db.execute("UPDATE positions SET open_qty=open_qty-? WHERE id=?",
                            (quantity, key))
            # An alert created before a partial/full fill references an outdated
            # open quantity. It must never be delivered as a current sell prompt.
            self.db.execute(
                "UPDATE outbox SET state='INVALIDATED_BY_FILL' "
                "WHERE position_id=? AND state IN ('PENDING', 'CLAIMED')", (key,)
            )
            # Revalidate against a new quote and new remaining quantity.
            self.db.execute(
                "UPDATE monitor SET armed=1 WHERE position_id=?", (key,)
            )
        return self.get(key), net, True

    def on_quote(
        self, key: str, quote: OptionQuote, now: datetime,
        max_age: timedelta, target_net_profit: Decimal,
        last_trading_at: datetime | None = None,
    ) -> MonitorResult:
        """Queue one *review* alert per upward crossing; never submit an order."""
        stamp(now)
        money(target_net_profit)
        with self._transaction():
            snap = self.get(key)
            if snap.status is PositionStatus.CLOSED:
                return MonitorResult(MonitorDecision.CLOSED)
            result = assess_profit_target(snap.remaining(), quote, now,
                                          max_age, target_net_profit, last_trading_at)
            errors = {
                AlertStatus.SUPPRESSED_CONTRACT_MISMATCH: MonitorDecision.CONTRACT_MISMATCH,
                AlertStatus.SUPPRESSED_UNQUALIFIED_FEED: MonitorDecision.UNQUALIFIED_FEED,
                AlertStatus.SUPPRESSED_STALE_QUOTE: MonitorDecision.STALE_QUOTE,
                AlertStatus.SUPPRESSED_INSUFFICIENT_SIZE: MonitorDecision.INSUFFICIENT_SIZE,
                AlertStatus.SUPPRESSED_EXPIRED: MonitorDecision.EXPIRED,
                AlertStatus.SUPPRESSED_CUTOFF_UNVERIFIED: MonitorDecision.CUTOFF_UNVERIFIED,
                AlertStatus.SUPPRESSED_AFTER_CUTOFF: MonitorDecision.AFTER_CUTOFF,
                AlertStatus.SUPPRESSED_WRONG_TRADING_DATE: MonitorDecision.WRONG_TRADING_DATE,
            }
            if result.status in errors:
                # A newer same-contract quote may make a previously pending
                # target review unsafe even if this replacement quote is not
                # itself actionable (unqualified feed, degraded depth, stale
                # transport or failed cutoff metadata). Cancel the old prompt
                # and re-arm ONLY for a subsequent strictly newer qualified
                # quote. Never let an old/bad-symbol quote cancel a fresh one.
                #
                # We deliberately do not treat a self-declared feed name as
                # independent real OPRA provenance: live dispatch is elsewhere
                # and remains disabled until actual source qualification.
                invalidation_states = {
                    AlertStatus.SUPPRESSED_INSUFFICIENT_SIZE: "INVALIDATED_LIQUIDITY",
                    AlertStatus.SUPPRESSED_UNQUALIFIED_FEED: "INVALIDATED_FEED_DEGRADED",
                    AlertStatus.SUPPRESSED_STALE_QUOTE: "INVALIDATED_FEED_STALE",
                    AlertStatus.SUPPRESSED_CUTOFF_UNVERIFIED: "INVALIDATED_CUTOFF_UNKNOWN",
                    AlertStatus.SUPPRESSED_AFTER_CUTOFF: "INVALIDATED_AFTER_CUTOFF",
                    AlertStatus.SUPPRESSED_EXPIRED: "INVALIDATED_AFTER_CUTOFF",
                    AlertStatus.SUPPRESSED_WRONG_TRADING_DATE: "INVALIDATED_TRADING_DATE",
                }
                if (result.status in invalidation_states and
                        quote.contract == snap.contract):
                    prior = self.db.execute(
                        "SELECT last_quote_at FROM monitor WHERE position_id=?",
                        (key,),
                    ).fetchone()
                    observed = stamp(quote.observed_at)
                    if prior["last_quote_at"] is None or observed > prior["last_quote_at"]:
                        self.db.execute(
                            "UPDATE outbox SET state=? "
                            "WHERE position_id=? AND state IN ('PENDING','CLAIMED')",
                            (invalidation_states[result.status], key),
                        )
                        if result.status is AlertStatus.SUPPRESSED_INSUFFICIENT_SIZE:
                            # Only a current, otherwise eligible NBBO-labeled
                            # depth failure can advance the quote watermark.
                            # Unqualified/stale/future-dated feeds must not
                            # control the ordering of later valid observations.
                            self.db.execute(
                                "UPDATE monitor SET armed=1,last_quote_at=? "
                                "WHERE position_id=?", (observed, key),
                            )
                        else:
                            self.db.execute(
                                "UPDATE monitor SET armed=1 WHERE position_id=?",
                                (key,),
                            )
                return MonitorResult(errors[result.status])
            observed = stamp(quote.observed_at)
            state = self.db.execute("SELECT * FROM monitor WHERE position_id=?", (key,)).fetchone()
            if state["last_quote_at"] is not None and observed <= state["last_quote_at"]:
                return MonitorResult(MonitorDecision.OUT_OF_ORDER)
            if result.status is AlertStatus.TARGET_NOT_REACHED:
                self.db.execute(
                    "UPDATE outbox SET state='INVALIDATED_PRICE_REVERSAL' "
                    "WHERE position_id=? AND state IN ('PENDING','CLAIMED')",
                    (key,),
                )
                self.db.execute(
                    "UPDATE monitor SET armed=1, last_quote_at=?, last_target=? "
                    "WHERE position_id=?", (observed, str(target_net_profit), key)
                )
                return MonitorResult(MonitorDecision.TARGET_NOT_REACHED,
                                     net_pnl_estimate=result.estimated_net_pnl)
            new_target = (state["last_target"] is None
                          or Decimal(state["last_target"]) != target_net_profit)
            if new_target:
                self.db.execute(
                    "UPDATE outbox SET state='INVALIDATED_TARGET_CHANGE' "
                    "WHERE position_id=? AND state IN ('PENDING','CLAIMED')",
                    (key,),
                )
            self.db.execute(
                "UPDATE monitor SET armed=0, last_quote_at=?, last_target=? "
                "WHERE position_id=?", (observed, str(target_net_profit), key)
            )
            if not state["armed"] and not new_target:
                return MonitorResult(MonitorDecision.ALREADY_NOTIFIED,
                                     net_pnl_estimate=result.estimated_net_pnl)
            notice_id = uuid4().hex
            quote_expiry = min(
                quote.observed_at + max_age, last_trading_at
            )
            self.db.execute(
                "INSERT INTO outbox "
                "(id,position_id,quote_at,bid,net,target,state,created_at,"
                "quote_expires_at,contract_cutoff_at) "
                "VALUES (?,?,?,?,?,?,'PENDING',?,?,?)",
                (notice_id, key, observed, str(quote.bid),
                 str(result.estimated_net_pnl), str(target_net_profit),
                 stamp(now), stamp(quote_expiry), stamp(last_trading_at)),
            )
            return MonitorResult(MonitorDecision.ALERT_QUEUED, notice_id,
                                 result.estimated_net_pnl)

    def pending(self, key: str) -> list[dict[str, str]]:
        rows = self.db.execute(
            "SELECT * FROM outbox WHERE position_id=? AND state='PENDING' "
            "ORDER BY created_at, id", (key,)
        )
        return [dict(row) for row in rows]

    def revalidation_candidates(
        self, key: str, *, now: datetime, max_quote_age: timedelta,
        last_trading_at: datetime | None,
    ) -> list[dict[str, str]]:
        """Atomically CLAIM each still-valid outbox row exactly once.

        This is only a one-time claim for external fresh-quote reassessment,
        NOT permission to send a sell signal. Claims are not retried after
        an uncertain provider result: avoid accidental duplicate pushes.
        """
        present = stamp(now)
        if not isinstance(max_quote_age, timedelta) or max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        if last_trading_at is None:
            return []
        cutoff = stamp(last_trading_at)
        with self._transaction():
            snap = self.get(key)
            if last_trading_at.astimezone(
                ZoneInfo("America/New_York")
            ).date() != snap.contract.expiration:
                raise ValueError("cutoff must be on the contract NY expiry date")
            monitor = self.db.execute(
                "SELECT armed,last_quote_at,last_target FROM monitor WHERE position_id=?",
                (key,),
            ).fetchone()
            rows = self.db.execute(
                "SELECT * FROM outbox WHERE position_id=? AND state='PENDING' "
                "ORDER BY created_at, id", (key,)
            ).fetchall()
            claimed = []
            for row in rows:
                quote_at = datetime.fromisoformat(row["quote_at"])
                age = now - quote_at
                expiry = row["quote_expires_at"]
                original_cutoff = row["contract_cutoff_at"]
                expired = (
                    expiry is None or original_cutoff is None or
                    snap.status is PositionStatus.CLOSED or
                    present >= cutoff or
                    (expiry is not None and present >= expiry) or
                    (original_cutoff is not None and present >= original_cutoff) or
                    age < timedelta(0) or age > max_quote_age or
                    monitor["armed"] or
                    monitor["last_target"] is None or
                    Decimal(monitor["last_target"]) != Decimal(row["target"]) or
                    monitor["last_quote_at"] is None or
                    row["quote_at"] > monitor["last_quote_at"]
                )
                if expired:
                    self.db.execute(
                        "UPDATE outbox SET state='INVALIDATED_STALE' WHERE id=?",
                        (row["id"],),
                    )
                    continue
                token = uuid4().hex
                updated = self.db.execute(
                    "UPDATE outbox SET state='CLAIMED',claim_token=?,claimed_at=? "
                    "WHERE id=? AND state='PENDING'",
                    (token, present, row["id"]),
                )
                if updated.rowcount == 1:
                    candidate = dict(row)
                    candidate["claim_token"] = token
                    candidate["state"] = "CLAIMED"
                    claimed.append(candidate)
            return claimed

    def record_provider_acceptance(
        self, notice_id: str, *, claim_token: str,
        accepted_at: datetime, max_quote_age: timedelta,
        last_trading_at: datetime | None,
        provider_accepted: bool, provider_receipt_id: str,
    ) -> bool:
        """Record external transport ACK without claiming a phone receipt or fill.

        The caller must use the one-time claim and *independently* recheck a
        fresh matching contract Bid just before sending. This layer validates
        immutable notice policy and basic event chronology, but cannot verify
        whether provider_accepted is a genuine remote service response.
        """
        accepted = stamp(accepted_at)
        if provider_accepted is not True:
            raise ValueError("actual provider acceptance must be explicitly confirmed")
        if not isinstance(claim_token, str) or len(claim_token) != 32:
            raise ValueError("a valid one-time claim token is required")
        if (not isinstance(provider_receipt_id, str) or
                not 1 <= len(provider_receipt_id) <= 256 or
                not provider_receipt_id.isprintable()):
            raise ValueError("provider receipt id required")
        if not isinstance(max_quote_age, timedelta) or max_quote_age <= timedelta(0):
            raise ValueError("max_quote_age must be positive")
        if last_trading_at is None:
            raise ValueError("contract cutoff unknown; cannot accept notification")
        cutoff = stamp(last_trading_at)
        digest = sha256(provider_receipt_id.encode("utf-8")).hexdigest()
        with self._transaction():
            row = self.db.execute(
                "SELECT * FROM outbox WHERE id=? AND claim_token=?",
                (notice_id, claim_token),
            ).fetchone()
            if row is None:
                raise KeyError(notice_id)
            snap = self.get(row["position_id"])
            if last_trading_at.astimezone(
                ZoneInfo("America/New_York")
            ).date() != snap.contract.expiration:
                raise ValueError("cutoff must be on NY expiry date")
            if accepted < row["created_at"] or accepted < row["claimed_at"]:
                raise ValueError("provider acknowledgement predates alert/claim")
            prior = self.db.execute(
                "SELECT * FROM provider_ack_events WHERE notice_id=?",
                (notice_id,),
            ).fetchone()
            if prior is not None:
                if prior["accepted_at"] != accepted or prior["receipt_digest"] != digest:
                    raise ValueError("conflicting provider acknowledgement")
                return bool(prior["considered_timely"])
            age = accepted_at - datetime.fromisoformat(row["quote_at"])
            invalid = (
                row["state"] != "CLAIMED" or
                snap.status is PositionStatus.CLOSED or
                row["quote_expires_at"] is None or
                row["contract_cutoff_at"] is None or
                accepted >= cutoff or
                (row["quote_expires_at"] is not None
                 and accepted >= row["quote_expires_at"]) or
                (row["contract_cutoff_at"] is not None
                 and accepted >= row["contract_cutoff_at"]) or
                age < timedelta(0) or age > max_quote_age
            )
            new_state = (
                "PROVIDER_ACCEPTED_TOO_LATE" if invalid
                else "ACCEPTED_BY_PROVIDER"
            )
            self.db.execute(
                "INSERT INTO provider_ack_events VALUES (?,?,?,?)",
                (notice_id, accepted, digest, int(not invalid)),
            )
            if row["state"] == "CLAIMED":
                self.db.execute(
                    "UPDATE outbox SET state=?,accepted_at=?,receipt_digest=? "
                    "WHERE id=? AND state='CLAIMED'",
                    (new_state, accepted, digest, notice_id),
                )
            else:
                # Preserve the reason for invalidation while keeping a
                # separate historical fact that the provider later ACKed.
                self.db.execute(
                    "UPDATE outbox SET accepted_at=?,receipt_digest=? WHERE id=?",
                    (accepted, digest, notice_id),
                )
            return not invalid
