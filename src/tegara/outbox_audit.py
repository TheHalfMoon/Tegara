"""Read-only local options notification outbox reconciliation.

Never retries, claims, delivers, or recognizes a broker/phone confirmation.
A provider acknowledgement is not a delivery receipt.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import os
import re
from pathlib import Path
import sqlite3
import stat

MAX_AUDIT_ROWS = 10_000
MAX_DB_BYTES = 32 * 1024 * 1024
KNOWN_STATES = {
    "PENDING", "CLAIMED", "ACCEPTED_BY_PROVIDER", "PROVIDER_ACCEPTED_TOO_LATE",
}


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("outbox timestamp missing or malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid outbox timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("outbox timestamp is not offset-aware")
    return parsed.astimezone(timezone.utc)


def _safe_existing_db(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)) or str(path) in ("", ":memory:"):
        raise ValueError("an existing on-disk SQLite database is required")
    raw = Path(path).expanduser().absolute()
    # macOS legitimately aliases /var -> /private/var for temp directories.
    # Canonicalize parent directories while rejecting a database *file*
    # that is itself a symlink. A nonexistent DB must never be created.
    if raw.is_symlink():
        raise ValueError("SQLite audit database file symlinks are refused")
    try:
        p = raw.resolve(strict=True)
        st = p.stat()
    except OSError as exc:
        raise ValueError("SQLite audit database is unavailable") from exc
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("SQLite audit requires an existing regular database file")
    if st.st_size > MAX_DB_BYTES:
        raise ValueError("SQLite audit database exceeds read-only safety budget")
    return p



def _offline_snapshot_bytes(path: str | Path) -> bytes:
    """Read a single bounded O_NOFOLLOW file descriptor, never reopen by path.

    This requires a quiescent main SQLite file: any WAL, SHM or journal
    sidecar means the main file might lack committed changes and is denied.
    """
    p = _safe_existing_db(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("atomic no-follow SQLite audit is unavailable")
    for suffix in ("-wal", "-journal", "-shm"):
        if Path(str(p) + suffix).exists():
            raise ValueError("SQLite sidecar exists; use a quiescent offline DB")
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        st = os.fstat(source.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_DB_BYTES:
            raise ValueError("SQLite snapshot requires a bounded regular file")
        data = source.read(MAX_DB_BYTES + 1)
        updated = os.fstat(source.fileno())
        if (len(data) != st.st_size or len(data) > MAX_DB_BYTES or
                (updated.st_dev, updated.st_ino, updated.st_size, updated.st_mtime_ns)
                != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)):
            raise ValueError("SQLite input changed during offline snapshot")
    for suffix in ("-wal", "-journal", "-shm"):
        if Path(str(p) + suffix).exists():
            raise ValueError("SQLite sidecar appeared during snapshot")
    return data


def _check_schema(conn: sqlite3.Connection) -> None:
    required = {
        "positions": {
            "id", "symbol", "right_type", "strike", "expiry", "multiplier",
            "entry_price", "initial_qty", "open_qty", "entry_fee", "exit_fee",
            "opened_at",
        },
        "outbox": {
            "id", "position_id", "quote_at", "bid", "net", "target", "state",
            "created_at", "quote_expires_at", "contract_cutoff_at", "claim_token",
            "claimed_at", "accepted_at", "receipt_digest",
        },
        "provider_ack_events": {
            "notice_id", "accepted_at", "receipt_digest", "considered_timely",
        },
    }
    actual = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not required.keys() <= actual:
        raise ValueError("unsupported/incomplete outbox schema")
    for table, fields in required.items():
        columns = {
            row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")
        }
        key = "notice_id" if table == "provider_ack_events" else "id"
        if not fields <= columns.keys() or columns[key]["pk"] != 1:
            raise ValueError("unsupported outbox schema fields or primary key")
    foreign = {
        row["from"]: (row["table"], row["to"])
        for row in conn.execute("PRAGMA foreign_key_list(outbox)")
    }
    ack_foreign = {
        row["from"]: (row["table"], row["to"])
        for row in conn.execute("PRAGMA foreign_key_list(provider_ack_events)")
    }
    if (foreign.get("position_id") != ("positions", "id")
            or ack_foreign.get("notice_id") != ("outbox", "id")):
        raise ValueError("unsupported outbox foreign-key schema")


def read_outbox_audit(
    path: str | Path, *, now: datetime, position_id: str | None = None
) -> dict:
    """Return bounded, sanitized counts without inspecting claim tokens."""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("audit time must be timezone aware")
    if position_id is not None and (
        not isinstance(position_id, str) or not 1 <= len(position_id) <= 128
    ):
        raise ValueError("position identifier must be 1..128 characters")
    payload = _offline_snapshot_bytes(path)
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.deserialize(payload)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        _check_schema(conn)
        if position_id is not None and not conn.execute(
            "SELECT 1 FROM positions WHERE id=?", (position_id,)
        ).fetchone():
            raise ValueError("position identifier does not exist")
        rows = conn.execute(
            "SELECT o.state,o.claimed_at,o.quote_expires_at,"
            "o.contract_cutoff_at,a.notice_id AS ack_notice,"
            "a.accepted_at AS ack_at,a.receipt_digest AS ack_digest,"
            "a.considered_timely AS ack_timely "
            "FROM outbox AS o LEFT JOIN provider_ack_events AS a ON a.notice_id=o.id "
            "WHERE (? IS NULL OR o.position_id=?) "
            "LIMIT ?", (position_id, position_id, MAX_AUDIT_ROWS + 1),
        ).fetchall()
        if len(rows) > MAX_AUDIT_ROWS:
            raise ValueError("notification outbox exceeds bounded audit row budget")
        counts: Counter[str] = Counter()
        expired_claims = 0
        unconfirmed = 0
        clock = now.astimezone(timezone.utc)
        for row in rows:
            status = row["state"]
            if not isinstance(status, str) or (
                status not in KNOWN_STATES and not status.startswith("INVALIDATED_")
            ):
                raise ValueError("unknown outbox state; cannot qualify audit")
            counts[status] += 1
            # Invalidation can overwrite state='CLAIMED' after a newer
            # adverse quote or manual fill. The remote send is STILL uncertain
            # unless a separate provider ACK event exists. Never lose those
            # claimed-then-invalidated notices from manual reconciliation.
            if status == "CLAIMED" and row["ack_notice"] is not None:
                raise ValueError("claimed notice has conflicting provider ACK")
            if status == "CLAIMED" and row["claimed_at"] is None:
                raise ValueError("claimed notice missing claim timestamp")
            if status == "PENDING" and (
                row["claimed_at"] is not None or row["ack_notice"] is not None
            ):
                raise ValueError("pending notice unexpectedly carries claim or ACK")
            if row["ack_notice"] is not None:
                if row["claimed_at"] is None:
                    raise ValueError("provider ACK without prior claim")
                if (not isinstance(row["ack_digest"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", row["ack_digest"]) is None
                        or type(row["ack_timely"]) is not int
                        or row["ack_timely"] not in (0, 1)):
                    raise ValueError("invalid provider ACK digest or status")
                received = _timestamp(row["ack_at"])
                claimed = _timestamp(row["claimed_at"])
                if received < claimed or received > clock:
                    raise ValueError("invalid provider ACK chronology")
            if row["claimed_at"] is not None and row["ack_notice"] is None:
                unconfirmed += 1
                claimed_at = _timestamp(row["claimed_at"])
                if claimed_at > clock:
                    raise ValueError("claimed notice appears in the future")
                # Absent immutable boundaries are unqualified and therefore
                # treated as expired/unsafe, NEVER as retryable.
                expiry = row["quote_expires_at"]
                cutoff = row["contract_cutoff_at"]
                if (expiry is None or cutoff is None
                        or clock >= _timestamp(expiry)
                        or clock >= _timestamp(cutoff)):
                    expired_claims += 1
            if status == "ACCEPTED_BY_PROVIDER" and row["ack_notice"] is None:
                raise ValueError("provider-accepted state lacks ACK evidence")
        conn.rollback()
    unack = unconfirmed
    return {
        "mode": "READ_ONLY_LOCAL_RECONCILIATION",
        "audited_at_utc": clock.isoformat(),
        "position_scope": "ONE_POSITION" if position_id is not None else "ALL_POSITIONS",
        "outbox_rows_checked": len(rows),
        "states": dict(sorted(counts.items())),
        "claimed_without_provider_ack": unack,
        "expired_or_unqualified_claims": expired_claims,
        "manual_reconciliation_required": unack > 0,
        "automatic_retry_authorized": False,
        "phone_delivery_verified": False,
        "broker_fill_verified": False,
        "source_options_feed_verified": False,
        "warning": (
            "Provider ACK is not device delivery, and CLAIMED is not a broker "
            "order. Never resend uncertain claims automatically. No notice "
            "IDs, receipt digests or claim tokens are disclosed."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    parser = argparse.ArgumentParser(description="Read-only Tegara outbox audit")
    parser.add_argument("--db", required=True, help="Existing local on-disk SQLite DB")
    parser.add_argument("--position-id", help="Optional exact local position identifier")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 2 for any unacknowledged claim")
    args = parser.parse_args(argv)
    try:
        report = read_outbox_audit(
            args.db, now=datetime.now(timezone.utc),
            position_id=args.position_id,
        )
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"Audit unavailable; no notification receipt claim: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if args.strict and report["manual_reconciliation_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
