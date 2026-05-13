# backend/app/services/review_pipeline.py
"""
InternX — 3-Layer Review Pipeline
Single entry point that runs all three review layers in sequence.

Layer 1: Deterministic Gate  — hard blocks on CI failure, secrets, bad diffs
Layer 2: ML Risk Scorer      — CodeBERT embeddings + structural heuristics
Layer 3: AI Mentor (Groq)    — non-blocking feedback, risk-context injected

Phase 3 addition: Layer 3 skip logic for low-risk PRs
  - If Layer 2 risk_label == "low" AND no critical secrets found in Layer 1,
    Layer 3 is skipped and a lightweight stub result is returned.
  - This saves tokens + latency for obviously clean PRs.
  - The frontend receives `layer3_skip_if_passing: true` and renders a
    "Low-risk PR — AI review skipped" message instead of the full mentor panel.
"""

from app.services.layer1_gate import run_layer1
from app.services.layer2_scorer import run_layer2      # real CodeBERT scorer
from app.services.mentor import run_layer3_groq         # renamed from review_pr_professional


# ── Layer 3 skip stub ─────────────────────────────────────────────────────────

def _build_l3_skip_result(task_id: str, intern_role: str, l2: dict) -> dict:
    """
    Returns a lightweight Layer 3 result when the PR is low-risk enough that
    a full Groq review would add noise rather than signal.

    The frontend uses `layer3_skip_if_passing: true` to render a
    "Low-risk PR — AI mentor skipped" message with the Layer 2 stats.
    """
    return {
        "version":              "2.0",
        "task_id":              task_id,
        "intern_role":          intern_role,
        "verdict":              "pass",
        "score":                85,
        "confidence":           0.75,
        "breakdown":            {},
        "strengths":            ["Low-risk PR — passed all deterministic gates."],
        "blocking_issues":      [],
        "missing_requirements": [],
        "improvements":         [],
        "review_summary":       (
            "This PR passed all Layer 1 gates (CI, secrets, diff sanity) "
            f"and Layer 2 scored it as low-risk "
            f"({l2.get('display', {}).get('badge', 'Risk: Low')}). "
            "Full AI mentor review was skipped for this submission."
        ),
        "next_steps":           ["Great work — your PR passed all checks. Mark this task as complete."],
        "audit_report":         None,
        "security_block":       False,
        "layer3_is_mentor_only":   True,
        "layer3_skip_if_passing":  True,
    }


# ── Pipeline entry point ──────────────────────────────────────────────────────

async def run_review_pipeline(
    task_id: str,
    pr_url: str,
    task_title: str,
    task_description: str,
    intern_role: str,
    internx_repo_url: str,
    commit_sha: str,
    repo_full_name: str,
    skip_l3_on_low_risk: bool = True,
) -> dict:
    """
    Runs the full 3-layer review pipeline.

    Returns a dict with layer1, layer2, layer3 results and a pipeline_verdict.
    Layer 1 is a hard gate: if it blocks, Layer 2 and Layer 3 never run.

    Phase 3 addition:
      If skip_l3_on_low_risk=True (default) and Layer 2 rates the PR as "low"
      risk with no critical secrets, Layer 3 is skipped and a lightweight
      pass result is returned in its place.
    """
    result = {
        "task_id":          task_id,
        "pr_url":           pr_url,
        "intern_role":      intern_role,
        "layer1":           None,
        "layer2":           None,
        "layer3":           None,
        "pipeline_verdict": "pending",
    }

    # ── Layer 1: Deterministic Gate ────────────────────────────────────────
    l1 = await run_layer1(pr_url, commit_sha, repo_full_name)
    result["layer1"] = l1

    if l1["verdict"] == "block":
        result["pipeline_verdict"] = "blocked_layer1"
        return result

    # ── Layer 2: ML Risk Scorer ────────────────────────────────────────────
    l2 = await run_layer2(l1["pr_diff"])
    result["layer2"] = l2

    # ── Phase 3: Layer 3 skip check ────────────────────────────────────────
    is_low_risk = l2.get("risk_label") == "low"
    has_critical_secret = l1.get("has_critical_secret", False)

    if skip_l3_on_low_risk and is_low_risk and not has_critical_secret:
        print(
            f"[Pipeline] ⚡ Layer 3 SKIPPED — risk={l2.get('risk_label')} "
            f"score={l2.get('risk_score')} no_secrets=True"
        )
        l3 = _build_l3_skip_result(task_id, intern_role, l2)
        result["layer3"] = l3
        result["pipeline_verdict"] = "pass"
        # Promote to top-level so the router never has to dig into layer3
        result["score"]      = l3.get("score", 85)
        result["verdict"]    = l3.get("verdict", "pass")
        result["confidence"] = l3.get("confidence", 0.75)
        result["breakdown"]  = l3.get("breakdown", {})
        return result

    # ── Layer 3: AI Mentor (Groq) ──────────────────────────────────────────
    print(
        f"[Pipeline] 🤖 Running Layer 3 — risk={l2.get('risk_label')} "
        f"type={l2.get('pr_type')} complexity={l2.get('complexity')}"
    )
    l3 = run_layer3_groq(
        task_id=task_id,
        task_title=task_title,
        task_description=task_description,
        pr_diff=l1["pr_diff"],
        intern_role=intern_role,
        internx_repo_url=internx_repo_url,
        risk_context=l2,
    )

    l3["layer3_is_mentor_only"]   = True
    l3["layer3_skip_if_passing"]  = False

    result["layer3"] = l3
    result["pipeline_verdict"] = l3.get("verdict", "complete")
    # Promote layer3 score/verdict/confidence/breakdown to top-level
    result["score"]      = l3.get("score") or 0
    result["verdict"]    = l3.get("verdict")
    result["confidence"] = l3.get("confidence", 0.5)
    result["breakdown"]  = l3.get("breakdown", {})

    return result