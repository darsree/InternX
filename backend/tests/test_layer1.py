"""
backend/tests/test_layer1_gate.py
Phase 5 — Layer 1 gate tests

Tests the deterministic gate without any AI calls.
All GitHub API calls are mocked so tests run offline.
"""

import pytest
from unittest.mock import patch, AsyncMock
from app.services.layer1_gate import run_layer1


# ── Clean PR fixtures ─────────────────────────────────────────────────────────

CLEAN_DIFF = """\
diff --git a/src/App.jsx b/src/App.jsx
index abc123..def456 100644
--- a/src/App.jsx
+++ b/src/App.jsx
@@ -1,5 +1,8 @@
 import React from 'react'
+import { useState } from 'react'
+
+const GREETING = 'Hello, world!'
 
 export default function App() {
-  return <div>Hello</div>
+  return <div>{GREETING}</div>
 }
"""

CI_PASS = {
    "passed":        True,
    "checks_run":    3,
    "failed_checks": [],
    "coverage_drop": False,
}

CI_FAIL = {
    "passed":        False,
    "checks_run":    2,
    "failed_checks": [
        {"name": "Frontend checks", "conclusion": "failure", "url": "https://github.com/checks/1"},
    ],
    "coverage_drop": False,
}

CI_EMPTY = {
    "passed":        True,
    "checks_run":    0,
    "failed_checks": [],
    "coverage_drop": False,
    "error":         "No commit SHA provided",
}


# ── Helper: patch both GitHub calls ──────────────────────────────────────────

def _mock_both(diff=CLEAN_DIFF, ci=CI_PASS):
    fetch_patch = patch("app.services.layer1_gate.fetch_pr_diff_from_github", return_value=diff)
    ci_patch    = patch("app.services.layer1_gate._fetch_ci_check_results",
                        new_callable=AsyncMock, return_value=ci)
    return fetch_patch, ci_patch


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_clean_pr():
    """A clean diff with all CI passing should result in verdict=pass."""
    fetch_p, ci_p = _mock_both()
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "abc123", "x/y")
    assert result["verdict"] == "pass"
    assert result["error_logs"] == []
    assert result["has_critical_secret"] is False
    assert result["pr_diff"] == CLEAN_DIFF


@pytest.mark.asyncio
async def test_blocks_on_ci_failure():
    """A failing CI check must block the pipeline."""
    fetch_p, ci_p = _mock_both(ci=CI_FAIL)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "abc123", "x/y")
    assert result["verdict"] == "block"
    assert len(result["error_logs"]) > 0
    assert any("Frontend checks" in log for log in result["error_logs"])


@pytest.mark.asyncio
async def test_blocks_on_openai_key_in_diff():
    """A diff containing an OpenAI API key must be blocked as a critical secret."""
    diff_with_secret = CLEAN_DIFF + "\n+OPENAI_API_KEY=sk-abcdefghij1234567890abcdefghij\n"
    fetch_p, ci_p = _mock_both(diff=diff_with_secret)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "abc123", "x/y")
    assert result["verdict"] == "block"
    assert result["has_critical_secret"] is True
    assert any("SECURITY BLOCK" in log for log in result["error_logs"])


@pytest.mark.asyncio
async def test_blocks_on_aws_key():
    """AWS Access Key ID must trigger a critical security block."""
    diff_with_aws = CLEAN_DIFF + "\n+AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
    fetch_p, ci_p = _mock_both(diff=diff_with_aws)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert result["verdict"] == "block"
    assert result["has_critical_secret"] is True


@pytest.mark.asyncio
async def test_blocks_on_github_pat():
    """A GitHub personal access token must block the PR."""
    diff_with_pat = CLEAN_DIFF + "\n+token=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456789\n"
    fetch_p, ci_p = _mock_both(diff=diff_with_pat)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert result["verdict"] == "block"
    assert result["has_critical_secret"] is True


@pytest.mark.asyncio
async def test_blocks_on_empty_diff():
    """An empty (None) diff must result in a block with an informative error log."""
    with patch("app.services.layer1_gate.fetch_pr_diff_from_github", return_value=None):
        result = await run_layer1("https://github.com/x/y/pull/1", "abc123", "x/y")
    assert result["verdict"] == "block"
    assert len(result["error_logs"]) > 0
    assert result["pr_diff"] == ""


@pytest.mark.asyncio
async def test_passes_with_no_commit_sha():
    """When commit_sha is empty, GitHub API returns an error but we fail-open."""
    fetch_p, ci_p = _mock_both(ci=CI_EMPTY)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "", "x/y")
    # CI_EMPTY.passed is True so no block from CI
    assert result["verdict"] == "pass"


@pytest.mark.asyncio
async def test_both_ci_fail_and_secret_blocked():
    """When both CI fails AND a secret is present, verdict must be block."""
    diff_with_secret = CLEAN_DIFF + "\n+OPENAI_API_KEY=sk-abcdefghij1234567890\n"
    fetch_p, ci_p = _mock_both(diff=diff_with_secret, ci=CI_FAIL)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert result["verdict"] == "block"
    assert result["has_critical_secret"] is True
    # Should have multiple error logs — one for CI, one for secret
    assert len(result["error_logs"]) >= 2


@pytest.mark.asyncio
async def test_result_contains_security_findings():
    """Layer 1 result must expose the full security findings list."""
    fetch_p, ci_p = _mock_both()
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert isinstance(result["security"], list)


@pytest.mark.asyncio
async def test_result_contains_pr_diff_on_pass():
    """The pr_diff must be passed through to Layer 2 on a passing gate."""
    fetch_p, ci_p = _mock_both()
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert result["pr_diff"] == CLEAN_DIFF
    assert len(result["pr_diff"]) > 10


@pytest.mark.asyncio
async def test_coverage_drop_is_reported():
    """A coverage-drop CI failure should appear in the error logs."""
    ci_with_cov = {
        "passed":        False,
        "checks_run":    2,
        "failed_checks": [{"name": "coverage-report", "conclusion": "failure", "url": "..."}],
        "coverage_drop": True,
    }
    fetch_p, ci_p = _mock_both(ci=ci_with_cov)
    with fetch_p, ci_p:
        result = await run_layer1("https://github.com/x/y/pull/1", "sha", "x/y")
    assert result["verdict"] == "block"
    assert any("coverage" in log.lower() for log in result["error_logs"])