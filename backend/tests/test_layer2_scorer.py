"""
backend/tests/test_layer2_scorer.py
Phase 5 — Layer 2 ML risk scorer tests

All CodeBERT model calls are mocked with np.zeros(768) so tests
run offline in CI without downloading the 500 MB model.
"""

import pytest
from unittest.mock import patch
import numpy as np
from app.services.layer2_scorer import (
    run_layer2,
    _parse_diff_stats,
    _classify_pr_type,
    _risk_label,
    _complexity_label,
    _stub_result,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

CLEAN_DIFF = """\
diff --git a/src/App.jsx b/src/App.jsx
@@ -1,3 +1,5 @@
+import { useState } from 'react'
+const x = 1
-const y = 2
"""

BUGFIX_DIFF = "fix: resolve crash in auth flow\n+ error handling added\n+ bug patched on line 42"
FEATURE_DIFF = "feat: add new user registration page\n+ implement create account flow\n+ new form introduced"
REFACTOR_DIFF = "refactor: extract helper functions\n+ simplify data processing\n+ rename util module"

LARGE_DIFF = "\n".join(
    [f"+line{i}" for i in range(400)] + [f"-line{i}" for i in range(200)]
)


# ── Stub / error handling ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_on_empty_diff():
    result = await run_layer2("")
    assert result["risk_label"] == "medium"
    assert "error" in result
    assert result["error"] != ""


@pytest.mark.asyncio
async def test_stub_on_whitespace_only():
    result = await run_layer2("   \n\n   ")
    assert result["risk_label"] == "medium"


def test_stub_result_structure():
    stub = _stub_result("test reason")
    required_keys = {"risk_score", "risk_label", "pr_type", "complexity", "diff_stats", "display"}
    assert required_keys.issubset(set(stub.keys()))
    assert stub["display"]["badge"] is not None


# ── PR type classifier ────────────────────────────────────────────────────────

def test_classifies_bugfix():
    assert _classify_pr_type(BUGFIX_DIFF) == "bugfix"


def test_classifies_feature():
    assert _classify_pr_type(FEATURE_DIFF) == "feature"


def test_classifies_refactor():
    assert _classify_pr_type(REFACTOR_DIFF) == "refactor"


def test_classify_defaults_to_highest_signal():
    """When signals tie, max() picks consistently."""
    result = _classify_pr_type("add new fix bug")
    assert result in ("bugfix", "feature", "refactor")


# ── Diff stats parser ─────────────────────────────────────────────────────────

def test_parse_diff_stats_single_file():
    diff = "diff --git a/f.py b/f.py\n@@ -1,3 +1,4 @@\n+new line\n-old line"
    stats = _parse_diff_stats(diff)
    assert stats["files_changed"] == 1
    assert stats["hunks"] == 1
    assert stats["lines_added"] == 1
    assert stats["lines_removed"] == 1
    assert stats["churn"] == 2


def test_parse_diff_stats_multiple_files():
    diff = (
        "diff --git a/a.py b/a.py\n@@ @@\n+line\n"
        "diff --git a/b.py b/b.py\n@@ @@\n+line\n+line2\n"
    )
    stats = _parse_diff_stats(diff)
    assert stats["files_changed"] == 2
    assert stats["hunks"] == 2


def test_parse_diff_stats_empty():
    stats = _parse_diff_stats("")
    assert stats["files_changed"] == 0
    assert stats["churn"] == 0


# ── Risk label ────────────────────────────────────────────────────────────────

def test_risk_label_low():
    assert _risk_label(0.1) == "low"
    assert _risk_label(0.34) == "low"


def test_risk_label_medium():
    assert _risk_label(0.35) == "medium"
    assert _risk_label(0.64) == "medium"


def test_risk_label_high():
    assert _risk_label(0.65) == "high"
    assert _risk_label(1.0) == "high"


# ── Complexity label ──────────────────────────────────────────────────────────

def test_complexity_low():
    stats = {"files_changed": 1, "hunks": 2}  # score=4
    assert _complexity_label(stats) == "low"


def test_complexity_medium():
    stats = {"files_changed": 3, "hunks": 5}  # score=11
    assert _complexity_label(stats) == "medium"


def test_complexity_high():
    stats = {"files_changed": 10, "hunks": 10}  # score=30
    assert _complexity_label(stats) == "high"


# ── run_layer2 end-to-end (mocked model) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_layer2_clean_diff():
    with patch("app.services.layer2_scorer.get_embedding", return_value=np.zeros(768)):
        result = await run_layer2(CLEAN_DIFF)
    assert result["risk_score"] is not None
    assert result["risk_label"] in ("low", "medium", "high")
    assert result["pr_type"] in ("bugfix", "feature", "refactor")
    assert result["complexity"] in ("low", "medium", "high")
    assert "display" in result
    assert "badge" in result["display"]


@pytest.mark.asyncio
async def test_run_layer2_high_churn():
    """600-line churn diff should produce medium or high risk."""
    with patch("app.services.layer2_scorer.get_embedding", return_value=np.zeros(768)):
        result = await run_layer2(LARGE_DIFF)
    assert result["risk_label"] in ("medium", "high")


@pytest.mark.asyncio
async def test_run_layer2_classifies_bugfix():
    with patch("app.services.layer2_scorer.get_embedding", return_value=np.zeros(768)):
        result = await run_layer2(BUGFIX_DIFF)
    assert result["pr_type"] == "bugfix"


@pytest.mark.asyncio
async def test_run_layer2_display_labels_are_strings():
    with patch("app.services.layer2_scorer.get_embedding", return_value=np.zeros(768)):
        result = await run_layer2(CLEAN_DIFF)
    d = result["display"]
    assert isinstance(d["badge"], str)
    assert isinstance(d["pr_type"], str)
    assert isinstance(d["complexity"], str)


@pytest.mark.asyncio
async def test_run_layer2_never_raises():
    """Layer 2 must never crash the pipeline — it always returns a result."""
    with patch("app.services.layer2_scorer.get_embedding", side_effect=RuntimeError("model crash")):
        result = await run_layer2(CLEAN_DIFF)
    # Should return a stub, not raise
    assert result["risk_label"] in ("low", "medium", "high")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_layer2_diff_stats_in_result():
    with patch("app.services.layer2_scorer.get_embedding", return_value=np.zeros(768)):
        result = await run_layer2(CLEAN_DIFF)
    stats = result["diff_stats"]
    assert "files_changed" in stats
    assert "churn" in stats