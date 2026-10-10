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



def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _detail_valid(value: object, pr: int) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("number")) is int and value["number"] == pr
        and _sha(value.get("headRefOid"))
        and isinstance(value.get("baseRefName"), str)
        and bool(value["baseRefName"])
        and type(value.get("isDraft")) is bool
        and value.get("state") == "OPEN"
    )


def _open_prs(repo: str, invoke: Callable[[list[str]], object]) -> list[dict]:
    """Read all pages, not just the first 100 draft/non-draft PRs."""
    combined: list[dict] = []
    seen: set[int] = set()
    for page in range(1, 21):
        rows = invoke([
            "api", f"repos/{repo}/pulls?state=open&per_page=100&page={page}",
        ])
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("incomplete or malformed GitHub pull request page")
        for r in rows:
            if (not isinstance(r, dict) or type(r.get("number")) is not int
                    or r["number"] < 1 or r["number"] in seen
                    or type(r.get("draft")) is not bool
                    or r.get("state") != "open"
                    or not isinstance(r.get("head"), dict)
                    or not _sha(r["head"].get("sha"))):
                raise ValueError("malformed, duplicate, or inconsistent open PR")
            seen.add(r["number"])
            combined.append(r)
        if len(rows) < 100:
            return combined
    raise ValueError("open PR pagination exceeded safe page budget")


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
    details = ["number", "headRefOid", "baseRefName", "isDraft", "state"]
    before = invoke(["pr", "view", str(pr), *args, "--json", ",".join(details)])
    if not _detail_valid(before, pr):
        raise ValueError("malformed, closed or inconsistent target pull request")
    old_head = before["headRefOid"]
    # Crucially: immutable commit-pinned status evidence. Mutable PR checks
    # without SHA attribution can lie if a branch moves A->B->A mid-query.
    run_data = invoke([
        "api", f"repos/{repo}/commits/{old_head}/check-runs?per_page=100",
    ])
    if not isinstance(run_data, dict) or type(run_data.get("total_count")) is not int:
        raise ValueError("malformed commit-specific GitHub check response")
    checks = run_data.get("check_runs")
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError("malformed commit-specific check collection")
    complete_checks = run_data["total_count"] == len(checks)
    if run_data["total_count"] < 0:
        raise ValueError("negative check collection count")
    listed = _open_prs(repo, invoke)
    target_in_list = [p for p in listed if p["number"] == pr]
    if len(target_in_list) != 1:
        raise ValueError("target PR missing from complete open inventory")
    after = invoke(["pr", "view", str(pr), *args, "--json", ",".join(details)])
    if not _detail_valid(after, pr):
        raise ValueError("target PR closed or metadata invalid during audit")
    stable = (
        before == after
        and target_in_list[0]["head"]["sha"] == old_head
        and target_in_list[0]["draft"] == before["isDraft"]
    )

    by_name: dict[str, list[dict]] = {}
    for check in checks:
        if (not isinstance(check, dict)
                or not isinstance(check.get("name"), str)
                or not isinstance(check.get("app"), dict)
                or not isinstance(check["app"].get("slug"), str)
                or not _sha(check.get("head_sha"))):
            raise ValueError("malformed commit check result")
        by_name.setdefault(check["name"], []).append(check)

    expected_apps = {
        "CodeQL": "github-advanced-security",
        "CodeQL python": "github-actions",
        "CodeQL javascript-typescript": "github-actions",
        "python-domain": "github-actions",
        "browser-e2e": "github-actions",
    }
    checked: dict[str, str] = {}
    for name in REQUIRED_CHECKS:
        matches = by_name.get(name, [])
        checked[name] = (
            "PASS" if stable and complete_checks and len(matches) == 1
            and matches[0].get("head_sha") == old_head
            and matches[0]["app"]["slug"] == expected_apps[name]
            and matches[0].get("status") == "completed"
            and matches[0].get("conclusion") == "success"
            else "NOT_PASS"
        )
    ci = (
        "PASS" if stable and complete_checks
        and all(v == "PASS" for v in checked.values())
        else "NOT_VERIFIED_OR_INCOMPLETE"
    )
    draft_count = sum(p["draft"] for p in listed)

    # NO automated claim is possible for external entitlements or reviews.
    # Independent signed/source-backed evidence must be added under
    # separately reviewed release governance before this can change.
    external = {gate: "NOT_INDEPENDENTLY_VERIFIED" for gate in EXTERNAL_GATES}
    blockers = (
        ["PR_HEAD_CHANGED_RECHECK"] if not stable else []
    ) + (
        ["COMMIT_CHECK_LIST_INCOMPLETE"] if not complete_checks else []
    ) + (
        ["CI_EXACT_HEAD_NOT_PASS"] if ci != "PASS" else []
    ) + (
        ["PR_IS_DRAFT"] if after["isDraft"] else []
    ) + (
        ["OPEN_UNMERGED_DRAFT_STACK"] if draft_count else []
    ) + list(external)
    return {
        "product": "Tegara",
        "repository": repo,
        "pull_request": pr,
        "head_sha": old_head if stable else None,
        "github_head_consistent_during_audit": stable,
        "software_ci": ci,
        "required_checks": checked,
        "ignored_review_checks": ["CodeRabbit"],  # success can mean skipped
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
