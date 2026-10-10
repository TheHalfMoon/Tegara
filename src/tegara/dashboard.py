"""Local loopback-only research dashboard and offline target calculations.

NO live market data, broker connection, notification delivery, or real trading.
Do not bind outside 127.0.0.1 or advertise option-target prices as quotes.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from .core import (
    LongOptionPosition, OptionContract, calculate_net_exit_pnl,
    minimum_bid_for_net_profit, minimum_bid_for_net_return_percent,
)

WEB_ROOT = Path(__file__).with_name("web")
MAX_BODY_BYTES = 16384


def calculate_payload(payload: dict) -> dict[str, str]:
    """Pure input-to-output calculation with no network or account access."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be an object")
    required = {
        "symbol", "right", "strike", "expiration", "entry_price",
        "quantity", "multiplier", "entry_fee_per_contract", "exit_fee_per_contract", "tick",
        "target_kind", "target_value",
    }
    if set(payload) != required:
        raise ValueError("unexpected or missing fields")

    def d(field: str) -> Decimal:
        value = payload[field]
        if not isinstance(value, str) or len(value) > 50:
            raise ValueError(f"{field} must be a decimal string")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{field} must be a decimal number") from exc
        if not number.is_finite() or number < 0:
            raise ValueError(f"{field} must be finite and nonnegative")
        return number

    qty = payload["quantity"]
    if type(qty) is not int or qty <= 0 or qty > 100000:
        raise ValueError("quantity must be a positive integer")
    symbol, right = payload["symbol"], payload["right"]
    if not isinstance(symbol, str) or not isinstance(right, str):
        raise ValueError("invalid option identity")
    if not isinstance(payload["expiration"], str):
        raise ValueError("expiration must be ISO date string")
    multiplier = payload["multiplier"]
    if type(multiplier) is not int or multiplier <= 0 or multiplier > 100000:
        raise ValueError("multiplier must be a positive integer")
    contract = OptionContract(
        symbol, right, d("strike"), date.fromisoformat(payload["expiration"]),
        multiplier,
    )
    position = LongOptionPosition(
        contract, d("entry_price"), qty,
        d("entry_fee_per_contract"), d("exit_fee_per_contract"),
    )
    tick = d("tick")
    # Option tick sizes must be bounded before dividing by tick: a hostile
    # decimal exponent can otherwise raise decimal.Overflow in HTTP handler.
    if not Decimal("0.00000001") <= tick <= Decimal("1000000"):
        raise ValueError("tick must be within 0.00000001..1000000")
    kind = payload["target_kind"]
    if kind == "NET_PROFIT_USD":
        raw_target = minimum_bid_for_net_profit(position, d("target_value"))
    elif kind == "NET_RETURN_PERCENT":
        raw_target = minimum_bid_for_net_return_percent(position, d("target_value"))
    else:
        raise ValueError("target_kind is not supported")
    rounded = (raw_target / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    return {
        "symbol": contract.symbol,
        "right": contract.right,
        "expiry": contract.expiration.isoformat(),
        "quantity": str(qty),
        "initial_debit_usd": str(position.initial_debit),
        "minimum_theoretical_bid": str(raw_target),
        "candidate_bid_target": str(rounded),
        "estimated_net_pnl_usd": str(calculate_net_exit_pnl(position, rounded)),
        "status": "HYPOTHETICAL_RESEARCH_ONLY",
        "quote_source": "NONE",
        "disclaimer": (
            "No broker order, no live option quote, no executable price, no "
            "guaranteed fill. Use correct real fees and verified contract tick rules."
        ),
    }


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "TegaraPrototype/0.1"

        def _allowed_host(self) -> bool:
            host = self.headers.get("Host", "")
            return host in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            )

        def _send(self, code: int, body: bytes, mime: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", (
                "default-src 'none'; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'"
            ))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj, separators=(",", ":")).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._json(403, {"error": "host not permitted"})
                return
            path = urlparse(self.path).path
            files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }
            if path == "/api/health":
                self._json(200, {
                    "status": "RESEARCH_ONLY", "market_data": "UNAVAILABLE",
                    "broker_orders": "DISABLED", "notifications": "DISABLED"
                })
                return
            if path not in files:
                self._json(404, {"error": "not found"})
                return
            filename, mime = files[path]
            self._send(200, (WEB_ROOT / filename).read_bytes(), mime)

        def do_POST(self) -> None:
            if not self._allowed_host():
                self._json(403, {"error": "host not permitted"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in (
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            ):
                self._json(403, {"error": "origin not permitted"})
                return
            if urlparse(self.path).path != "/api/target":
                self._json(404, {"error": "not found"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._json(415, {"error": "JSON only"})
                return
            length_str = self.headers.get("Content-Length", "")
            try:
                length = int(length_str)
            except ValueError:
                self._json(411, {"error": "Content-Length required"})
                return
            if length < 1 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "invalid payload size"})
                return
            try:
                body = json.loads(self.rfile.read(length))
                result = calculate_payload(body)
            except (ValueError, TypeError, json.JSONDecodeError,
                    OverflowError, DecimalException) as exc:
                self._json(422, {"error": str(exc)})
                return
            self._json(200, result)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log URL parameters, position attributes or financial details.
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Tegara research-only local dashboard")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler()) as server:
        print(f"Tegara research dashboard: http://127.0.0.1:{args.port}", flush=True)
        print("No live quotes, broker orders or mobile alerts.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
