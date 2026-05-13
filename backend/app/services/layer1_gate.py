# backend/app/services/layer1_gate.py
"""
InternX — Layer 1: Deterministic Gate

Hard-gates PRs before any AI runs. Zero ML involved.
Checks:
  1. Fetch PR diff from GitHub (fail fast if unreachable)
  2. CI check-run status via GitHub Checks API
  3. Secret / PII regex scan (reuses existing deterministic_security_scan)
  4. Diff sanity precheck (reuses existing precheck_diff)

Verdict: "pass" → pipeline continues to Layer 2
         "block" → pipeline stops; error_logs explain why
"""

import httpx
from app.core.config import get_settings
from app.services.mentor import (
    deterministic_security_scan,   # ✅ already exists — reused
    precheck_diff,                  # ✅ already exists — reused
    fetch_pr_diff_from_github,      # ✅ already exists — reused
)

settings = get_settings()


# ── CI Check Results ────────────────────────────────────────────────────────────

async def _fetch_ci_check_results(repo_full_name: str, commit_sha: str) -> dict:
    """
    Calls the GitHub Check Runs API for a specific commit.

    Returns:
        {
            "passed":        bool,
            "checks_run":    int,
            "failed_checks": [ { name, conclusion, url } ],
            "coverage_drop": bool,
            "error":         str | None,   # only present on infra failure
        }

    Fail-open policy: if GitHub is unreachable or returns non-200,
    we let the pipeline continue (don't block on infra issues).
    """
    url = (
        f"https://api.github.com/repos/{repo_full_name}"
        f"/commits/{commit_sha}/check-runs"
    )
    headers = {
        "Authorization": f"Bearer {settings.github_org_token}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            # Network-level failure — fail open so infra blips don't block interns
            return {
                "passed": True,
                "checks_run": 0,
                "failed_checks": [],
                "coverage_drop": False,
                "error": f"Network error reaching GitHub: {exc}",
            }

    if resp.status_code != 200:
        return {
            "passed": True,
            "checks_run": 0,
            "failed_checks": [],
            "coverage_drop": False,
            "error": f"GitHub API returned {resp.status_code}: {resp.text[:200]}",
        }

    data = resp.json()
    checks = data.get("check_runs", [])

    # A check is "failed" if its conclusion is anything other than
    # success, skipped, neutral, or still in-progress (None = still running)
    failed = [
        {
            "name":       c["name"],
            "conclusion": c["conclusion"],
            "url":        c["html_url"],
        }
        for c in checks
        if c.get("conclusion") not in ("success", "skipped", "neutral", None)
    ]

    # Detect coverage drop: pytest-cov + GitHub coverage-report action uploads
    # annotations with "coverage" in the output title when coverage falls
    coverage_drop = any(
        "coverage" in (c.get("output", {}).get("title") or "").lower()
        and c.get("conclusion") == "failure"
        for c in checks
    )

    return {
        "passed":        len(failed) == 0,
        "checks_run":    len(checks),
        "failed_checks": failed,
        "coverage_drop": coverage_drop,
    }


# ── Main Gate ───────────────────────────────────────────────────────────────────

async def run_layer1(pr_url: str, commit_sha: str, repo_full_name: str) -> dict:
    """
    Layer 1 hard gate. Runs three deterministic checks in sequence.

    Args:
        pr_url:          Full GitHub PR URL (e.g. https://github.com/org/repo/pull/42)
        commit_sha:      Head commit SHA for CI check lookup
        repo_full_name:  GitHub repo slug (e.g. "internx-hub/shopsphere-backend")

    Returns:
        {
            "verdict":             "pass" | "block",
            "pr_diff":             str,          # passed through to Layer 2
            "ci":                  dict,         # CI check results
            "security":            list[dict],   # all security findings (any severity)
            "has_critical_secret": bool,
            "precheck":            dict,         # { is_valid, reason }
            "error_logs":          list[str],    # human-readable block reasons
        }
    """
    error_logs: list[str] = []

    # ── Step 1: Fetch PR diff ──────────────────────────────────────────────
    pr_diff = fetch_pr_diff_from_github(pr_url)
    if not pr_diff:
        return {
            "verdict":             "block",
            "pr_diff":             "",
            "ci":                  {},
            "security":            [],
            "has_critical_secret": False,
            "precheck":            {"is_valid": False, "reason": "Could not fetch PR diff"},
            "error_logs": [
                "❌ Could not fetch PR diff. "
                "Check the PR URL and ensure the repo is accessible."
            ],
        }

    # ── Step 2: CI check results ───────────────────────────────────────────
    ci = await _fetch_ci_check_results(repo_full_name, commit_sha)

    if not ci["passed"]:
        for fc in ci.get("failed_checks", []):
            error_logs.append(
                f"❌ CI check failed: {fc['name']} → {fc['conclusion']} ({fc['url']})"
            )
        if ci.get("coverage_drop"):
            error_logs.append(
                "⚠️ Coverage drop detected — tests were removed or skipped."
            )

    # ── Step 3: Deterministic secret / PII scan ────────────────────────────
    security_findings = deterministic_security_scan(pr_diff)   # reuse existing
    has_critical_secret = any(
        f.get("severity") == "critical" for f in security_findings
    )
    if has_critical_secret:
        for f in security_findings:
            if f.get("severity") == "critical":
                error_logs.append(
                    f"🔴 SECURITY BLOCK: {f['label']} detected at diff line {f['line']}"
                )

    # ── Step 4: Diff sanity precheck ───────────────────────────────────────
    precheck = precheck_diff(pr_diff, [])                       # reuse existing
    if not precheck["is_valid"]:
        error_logs.append(f"❌ Diff rejected: {precheck['reason']}")

    # ── Verdict ────────────────────────────────────────────────────────────
    blocked = (
        not ci["passed"]
        or has_critical_secret
        or not precheck["is_valid"]
    )

    return {
        "verdict":             "block" if blocked else "pass",
        "pr_diff":             pr_diff,
        "ci":                  ci,
        "security":            security_findings,
        "has_critical_secret": has_critical_secret,
        "precheck":            precheck,
        "error_logs":          error_logs,
    }