"""Read-only exact-head Tegara research/release readiness audit.

Never treats CI, a skipped third-party review, or user-supplied quote labels
as independent Alibaba OCR, OPRA entitlements, phone receipt or broker fills.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable

REPOSITORY = "TheHalfMoon/Tegara"
REQUIRED_CHECKS = (
    "python-domain",
    "browser-e2e",
    "CodeQL",
    "CodeQL python",
    "CodeQL javascript-typescript",
)
EXTERNAL_GATES = (
    "independent_alibaba_ocr_exact_head_review",
    "licensed_source_verified_options_bid_ask",
    "exchange_contract_trading_cutoff_verified",
    "tradingview_pine_editor_compilation",
    "actual_phone_delivery_receipt",
    "derayah_global_manual_fill_reconciliation",
    "prospective_paper_trading_qualification",
)


def gh_json(args: list[str]) -> object:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True, text=True, check=False, timeout=45,
    )
    if proc.returncode:
        raise RuntimeError(
            "GitHub read failed; live repository checks were not qualified: "
            + proc.stderr.strip()[:240]
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub returned non-JSON state; do not claim CI success") from exc


def audit(
    *,
    repo: str = REPOSITORY,
    pr: int = 34,
    invoke: Callable[[list[str]], object] = gh_json,
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid GitHub owner/repository locator")
    if type(pr) is not int or not 1 <= pr <= 1000000:
        raise ValueError("pull request number is out of bounds")
    args = ["--repo", repo]
    details = ["number", "headRefOid", "baseRefName", "isDraft"]
    before = invoke(["pr", "view", str(pr), *args, "--json", ",".join(details)])
    checks = invoke([
        "pr", "checks", str(pr), *args, "--json", "name,state,bucket",
    ])
    listed = invoke([
        "pr", "list", *args, "--state", "open", "--limit", "100",
        "--json", "number,isDraft,headRefOid",
    ])
    after = invoke(["pr", "view", str(pr), *args, "--json", ",".join(details)])
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("malformed pull request metadata")
    if not isinstance(checks, list) or not isinstance(listed, list):
        raise ValueError("malformed pull request or CI collection")
    old_head = before.get("headRefOid")
    new_head = after.get("headRefOid")
    stable = (
        isinstance(old_head, str)
        and bool(re.fullmatch(r"[0-9a-f]{40}", old_head))
        and old_head == new_head
        and before.get("number") == pr == after.get("number")
    )
    by_name: dict[str, list[dict]] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise ValueError("malformed check result")
        by_name.setdefault(check["name"], []).append(check)

    checked: dict[str, str] = {}
    for name in REQUIRED_CHECKS:
        matches = by_name.get(name, [])
        checked[name] = (
            "PASS" if stable and len(matches) == 1
            and matches[0].get("bucket") == "pass"
            and matches[0].get("state") == "SUCCESS"
            else "NOT_PASS"
        )
    ci = (
        "PASS" if stable and all(v == "PASS" for v in checked.values())
        else "NOT_VERIFIED_OR_INCOMPLETE"
    )
    if any(not isinstance(p, dict) or type(p.get("isDraft")) is not bool
           for p in listed):
        raise ValueError("malformed open pull requests")
    draft_count = sum(p["isDraft"] for p in listed)

    # NO automated claim is possible for external entitlements or reviews.
    # Neither github check names nor a Boolean typed by a user can certify
    # these. Independent signed/source-backed evidence must be added under
    # separately reviewed release governance before this tool can change.
    external = {gate: "NOT_INDEPENDENTLY_VERIFIED" for gate in EXTERNAL_GATES}
    blockers = (
        ["PR_HEAD_CHANGED_RECHECK"] if not stable else []
    ) + (
        ["CI_EXACT_HEAD_NOT_PASS"] if ci != "PASS" else []
    ) + (
        ["PR_IS_DRAFT"] if after.get("isDraft") is not False else []
    ) + (
        ["OPEN_UNMERGED_DRAFT_STACK"] if draft_count else []
    ) + list(external)
    return {
        "product": "Tegara",
        "repository": repo,
        "pull_request": pr,
        "head_sha": new_head if stable else None,
        "github_head_consistent_during_audit": stable,
        "software_ci": ci,
        "required_checks": checked,
        "ignored_review_checks": ["CodeRabbit"],  # pass can mean skipped
        "open_pull_requests": len(listed),
        "open_draft_pull_requests": draft_count,
        "native_independent_review_and_external_evidence": external,
        "release_status": "BLOCKED_RESEARCH_ONLY",
        "live_options_trading_authorized": False,
        "phone_options_sell_alert_qualified": False,
        "broker_order_submitted": False,
        "blockers": blockers,
        "warning": (
            "Read-only GitHub audit; software CI is not proof of OPRA license, "
            "Alibaba OpenCodeReview approval, Derayah executions, or alerts "
            "arriving on a phone. No trades, pushes or purchases are made."
        ),
    }


def main(
    argv: list[str] | None = None,
    *,
    invoke: Callable[[list[str]], object] = gh_json,
) -> int:
    p = argparse.ArgumentParser(description="Tegara read-only release blocker audit")
    p.add_argument("--repo", default=REPOSITORY)
    p.add_argument("--pr", type=int, default=34)
    p.add_argument("--strict", action="store_true",
                   help="Exit 2 when any live release blockers remain")
    args = p.parse_args(argv)
    try:
        result = audit(repo=args.repo, pr=args.pr, invoke=invoke)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Audit unavailable; no readiness claim: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if args.strict and result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
