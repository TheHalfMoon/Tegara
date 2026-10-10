"""Optional macOS *local demonstration* of research target notifications.

This must never consume an assumed-live option quote or trigger a Derayah order.
Manual inputs are unverified. AppleScript is invoked with fixed script code and
separate CLI arguments to avoid running user-controlled source code.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import subprocess
import sys

from .core import (
    LongOptionPosition, calculate_net_exit_pnl, minimum_bid_for_net_profit,
)

SCRIPT = (
    "on run argv",
    "set titleText to item 1 of argv",
    "set bodyText to item 2 of argv",
    "display notification bodyText with title titleText",
    "end run",
)


@dataclass(frozen=True)
class ResearchNotice:
    title: str
    body: str
    status: str = "LOCAL_MANUAL_RESEARCH_DEMO"
    market_data_verified: bool = False
    phone_delivery_verified: bool = False
    broker_execution_verified: bool = False

    def __post_init__(self) -> None:
        if (self.status != "LOCAL_MANUAL_RESEARCH_DEMO"
                or self.market_data_verified is not False
                or self.phone_delivery_verified is not False
                or self.broker_execution_verified is not False):
            raise ValueError("demo cannot represent verified market, phone, or broker state")
        if not self.title.startswith("Tegara RESEARCH DEMO"):
            raise ValueError("cannot create an unlabelled live-like notification")
        if "MANUAL BID / NOT VERIFIED" not in self.body:
            raise ValueError("demo notice must state manual feed is unverified")
        if len(self.title) > 120 or len(self.body) > 340:
            raise ValueError("notification is too long")


def make_manual_demo_notice(
    position: LongOptionPosition,
    *, manual_bid: Decimal, target_net_profit: Decimal,
) -> ResearchNotice:
    """Build an untrusted-price comparison; NOT a sell alert or recommendation."""
    if not isinstance(manual_bid, Decimal) or not manual_bid.is_finite() or manual_bid < 0:
        raise ValueError("manual_bid must be nonnegative finite Decimal")
    if (not isinstance(target_net_profit, Decimal)
            or not target_net_profit.is_finite() or target_net_profit < 0):
        raise ValueError("target_net_profit must be nonnegative finite Decimal")
    contract = position.contract
    target = minimum_bid_for_net_profit(position, target_net_profit)
    estimated = calculate_net_exit_pnl(position, manual_bid)
    meets = manual_bid >= target
    title = "Tegara RESEARCH DEMO — manually entered option bid"
    direction = "ABOVE" if meets else "BELOW"
    body = (
        f"MANUAL BID / NOT VERIFIED: {contract.symbol} "
        f"{contract.expiration.isoformat()} {contract.strike} {contract.right}, "
        f"x{position.quantity}. Bid {manual_bid} is {direction} hypothetical "
        f"target {target:.4f}. Estimated P&L USD {estimated:.2f}. "
        "CHECK DERAYAH; NO ORDER PLACED."
    )
    return ResearchNotice(title, body)


def show_macos_demo(notice: ResearchNotice, *, explicitly_authorized: bool = False) -> bool:
    """Ask the macOS Notification Center to display a demo, not a confirmed receipt.

    Success means only that the local AppleScript process returned exit 0.
    System Focus/notification settings may silently suppress visible delivery.
    """
    if explicitly_authorized is not True:
        raise ValueError("explicit send-demo authorization required")
    if sys.platform != "darwin":
        raise RuntimeError("only macOS Notification Center is supported")
    command = ["osascript"]
    for line in SCRIPT:
        command.extend(["-e", line])
    command.extend(["--", notice.title, notice.body])
    result = subprocess.run(command, text=True, capture_output=True,
                            timeout=8, check=False)
    if result.returncode:
        raise RuntimeError("macOS notification script failed")
    return True
