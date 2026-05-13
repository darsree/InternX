"""
backend/tests/test_review_pipeline.py
Phase 5 — Integration tests for the full 3-layer review pipeline

All external calls (GitHub, Groq, CodeBERT) are mocked.
Tests verify the pipeline's routing logic, not the AI quality.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import numpy as np

CLEAN_DIFF = """\
diff --git a/src/app.py b/src/app.py
@@ -1,3 +1,5 @@
+from flask import Flask
+app = Flask(__name__)
-# placeholder
"""

L1_PASS = {
    "verdict":             "pass",
    "pr_diff":             CLEAN_DIFF,
    "ci":                  {"passed": True, "checks_run": 3, "failed_checks": [], "coverage_drop": False},
    "security":            [],
    "has_critical_secret": False,
    "precheck":            {"is_valid": True, "reason": "OK"},
    "error_logs":          [],
}

L1_BLOCK = {
    "verdict":             "block",
    "pr_diff":             "",
    "ci":                  {"passed": False, "checks_run": 1, "failed_checks": [
        {"name": "Backend tests", "conclusion": "failure", "url": "..."}
    ], "coverage_drop": False},
    "security":            [],
    "has_critical_secret": False,
    "precheck":            {"is_valid": True, "reason": "OK"},
    "error_logs":          ["❌ CI check failed: Backend tests → failure (...)"],
}

L2_LOW = {
    "risk_score": 0.1, "risk_label": "low",
    "pr_type": "bugfix", "complexity": "low",
    "diff_stats": {"files_changed": 1, "churn": 5, "hunks": 1},
    "display": {"badge": "Risk Score: 10% (Low)", "pr_type": "PR Type: Bugfix", "complexity": "Review Complexity: Low"},
}

L2_HIGH = {
    "risk_score": 0.8, "risk_label": "high",
    "pr_type": "feature", "complexity": "high",
    "diff_stats": {"files_changed": 12, "churn": 500, "hunks": 30},
    "display": {"badge": "Risk Score: 80% (High)", "pr_type": "PR Type: Feature", "complexity": "Review Complexity: High"},
}

L3_PASS = {
    "version": "2.0", "task_id": "t1", "intern_role": "backend",
    "verdict": "pass", "score": 85, "confidence": 0.9,
    "breakdown": {}, "strengths": ["Good code"],
    "blocking_issues": [], "missing_requirements": [],
    "improvements": [], "review_summary": "Looks great.",
    "next_steps": ["Well done!"],
    "audit_report": None, "security_block": False,
}


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_blocked_at_layer1():
    """When Layer 1 blocks, Layer 2 and Layer 3 must NOT run."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_BLOCK) as mock_l1, \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock) as mock_l2, \
         patch("app.services.review_pipeline.run_layer3_groq") as mock_l3:

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
        )

    assert result["pipeline_verdict"] == "blocked_layer1"
    assert result["layer1"]["verdict"] == "block"
    assert result["layer2"] is None
    assert result["layer3"] is None
    mock_l2.assert_not_called()
    mock_l3.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_skips_layer3_on_low_risk():
    """A low-risk PR should skip Layer 3 (Groq) entirely."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_PASS), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=L2_LOW), \
         patch("app.services.review_pipeline.run_layer3_groq") as mock_l3:

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
            skip_l3_on_low_risk=True,
        )

    mock_l3.assert_not_called()
    assert result["pipeline_verdict"] == "pass"
    assert result["layer3"]["layer3_skip_if_passing"] is True
    assert result["layer3"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_pipeline_runs_layer3_on_high_risk():
    """A high-risk PR must always run Layer 3."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_PASS), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=L2_HIGH), \
         patch("app.services.review_pipeline.run_layer3_groq", return_value=L3_PASS) as mock_l3:

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
        )

    mock_l3.assert_called_once()
    assert result["layer3"]["verdict"] == "pass"
    assert result["layer3"]["layer3_skip_if_passing"] is False


@pytest.mark.asyncio
async def test_pipeline_runs_layer3_when_skip_disabled():
    """When skip_l3_on_low_risk=False, Layer 3 always runs even for low-risk PRs."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_PASS), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=L2_LOW), \
         patch("app.services.review_pipeline.run_layer3_groq", return_value=L3_PASS) as mock_l3:

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
            skip_l3_on_low_risk=False,
        )

    mock_l3.assert_called_once()
    assert result["layer3"]["layer3_skip_if_passing"] is False


@pytest.mark.asyncio
async def test_pipeline_runs_layer3_on_low_risk_with_secret():
    """Even low-risk PRs must run Layer 3 if a critical secret was found."""
    from app.services.review_pipeline import run_review_pipeline

    l1_with_secret = {**L1_PASS, "has_critical_secret": True}
    l2_low = {**L2_LOW}

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=l1_with_secret), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=l2_low), \
         patch("app.services.review_pipeline.run_layer3_groq", return_value=L3_PASS) as mock_l3:

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
        )

    # Layer 3 must run because there's a critical secret, regardless of risk score
    mock_l3.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_result_shape():
    """Full pipeline result must have the expected top-level keys."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_PASS), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=L2_HIGH), \
         patch("app.services.review_pipeline.run_layer3_groq", return_value=L3_PASS):

        result = await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
        )

    required = {"task_id", "pr_url", "intern_role", "layer1", "layer2", "layer3", "pipeline_verdict"}
    assert required.issubset(set(result.keys()))


@pytest.mark.asyncio
async def test_pipeline_passes_diff_to_layer3():
    """Layer 3 must receive the diff from Layer 1 (not fetch it again)."""
    from app.services.review_pipeline import run_review_pipeline

    with patch("app.services.review_pipeline.run_layer1", new_callable=AsyncMock, return_value=L1_PASS), \
         patch("app.services.review_pipeline.run_layer2", new_callable=AsyncMock, return_value=L2_HIGH), \
         patch("app.services.review_pipeline.run_layer3_groq", return_value=L3_PASS) as mock_l3:

        await run_review_pipeline(
            task_id="t1", pr_url="https://github.com/x/y/pull/1",
            task_title="Test", task_description="desc",
            intern_role="backend", internx_repo_url="",
            commit_sha="abc", repo_full_name="x/y",
        )

    call_kwargs = mock_l3.call_args.kwargs
    assert call_kwargs["pr_diff"] == CLEAN_DIFF
    assert "risk_context" in call_kwargs