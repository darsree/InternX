"""
backend/app/routers/mentor.py
Complete mentor review router with Groq API support and proper async handling.
intern_role is read from the task record and forwarded to the review service.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import json
from datetime import datetime
import traceback

from app.core.auth import get_current_user
from app.core.database import get_supabase
from app.services.review_pipeline import run_review_pipeline  # CHANGED: was review_pr_professional
from fastapi import WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/api/mentor", tags=["mentor"])

def _fire_adaptive_on_done(user_id: str, task_id: str) -> None:
    """
    Calls the adaptive engine when a task is marked done via the review path.
    This is the missing link — the PATCH /status endpoint calls on_task_done,
    but the review background tasks set status=done directly in the DB without
    going through that endpoint, so we must call it here explicitly.
    Safe to call in a background thread; all errors are caught and logged.
    """
    import time
    time.sleep(1)  # small delay so DB write propagates
    try:
        from app.services.adaptive_engine import on_task_done
        result = on_task_done(user_id=user_id, task_id=task_id)
        print(f"[AdaptiveEngine] review-path on_task_done user={user_id} → {result.get('action','none')}")
    except Exception as e:
        print(f"[AdaptiveEngine] review-path on_task_done error: {e}")


# CHANGED: Added commit_sha and repo_full_name fields as required by run_review_pipeline
class ReviewRequest(BaseModel):
    task_id: str
    pr_url: str
    user_id: str
    override_role: Optional[str] = None  # Dev/test only: override intern_role from task
    commit_sha: Optional[str] = None     # NEW: head commit SHA
    repo_full_name: Optional[str] = None # NEW: e.g. "internx-hub/shopsphere-api"


class ReviewResponse(BaseModel):
    status: str
    attempt_id: Optional[str] = None
    message: Optional[str] = None


# ── New Pydantic Models ──────────────────────────────────────────────────────

class DesignReviewRequest(BaseModel):
    task_id: str
    user_id: str
    figma_url: Optional[str] = None
    explanation: str = ""
    handoff_checklist: dict = {}   # e.g. {"spacing": true, "colors": true, ...}
    image_base64: Optional[str] = None   # base64-encoded screenshot/export
    image_mime: Optional[str] = "image/png"


class QAReviewRequest(BaseModel):
    task_id: str
    user_id: str
    submission_type: str  # "bug_report" | "test_plan" | "automation_pr"
    # Bug report fields
    bug_title: Optional[str] = None
    bug_steps: Optional[str] = None
    bug_expected: Optional[str] = None
    bug_actual: Optional[str] = None
    bug_severity: Optional[str] = None   # critical | high | medium | low
    bug_environment: Optional[str] = None
    # Test plan fields
    test_plan_scope: Optional[str] = None
    test_cases: Optional[str] = None     # freeform text / numbered list
    test_coverage_areas: Optional[str] = None
    # Automation PR fields (reuses existing PR path but with tester rubric)
    pr_url: Optional[str] = None
    automation_framework: Optional[str] = None  # pytest | jest | cypress | playwright



@router.post("/review")
async def submit_review(
    request: ReviewRequest,
    background_tasks: BackgroundTasks
):
    """
    Submit a PR for AI review. Returns immediately with attempt ID.
    Actual review runs asynchronously.
    intern_role is read from the task record (tasks.intern_role column).
    Falls back to "default" if not set.
    """
    supabase = get_supabase()

    try:
        print(f"\n[SUBMIT] New review request: task={request.task_id}, user={request.user_id}")

        if not request.user_id or not request.user_id.strip():
            print(f"[SUBMIT] ❌ Rejected: user_id is missing or empty")
            return ReviewResponse(
                status="error",
                message="user_id is required. Please log in again."
            )

        if not isinstance(request.pr_url, str) or not request.pr_url.strip():
            return ReviewResponse(
                status="error",
                message="PR URL is required"
            )

        # Fetch task — include intern_role field
        print(f"[SUBMIT] Fetching task {request.task_id}...")
        task_result = supabase.table("tasks")\
            .select("*")\
            .eq("id", request.task_id)\
            .single()\
            .execute()

        if not task_result.data:
            return ReviewResponse(
                status="error",
                message="Task not found"
            )

        task = task_result.data
        # override_role is for dev/test panel only — lets you simulate any role
        # on any PR without being assigned that role.
        if request.override_role and request.override_role.strip():
            intern_role = request.override_role.strip().lower()
            print(f"[SUBMIT] ⚠️  Role OVERRIDDEN by test panel: {intern_role}")
        else:
            intern_role = (task.get("intern_role") or "default").strip().lower()
        print(f"[SUBMIT] ✓ Found task: {task.get('title')} | role: {intern_role}")

        # Create review_attempts record BEFORE updating the task status.
        print(f"[SUBMIT] Creating review_attempts record...")
        # NOTE: review_attempts has no intern_role column — role is read from tasks.intern_role
        attempt_result = supabase.table("review_attempts").insert({
            "task_id":   request.task_id,
            "user_id":   request.user_id.strip(),
            "pr_url":    request.pr_url.strip(),
            "ai_model":  "llama-3.3-70b-versatile",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        if not attempt_result.data:
            print(f"[SUBMIT] ❌ Failed to create attempt record")
            return ReviewResponse(
                status="error",
                message="Failed to create review attempt"
            )

        attempt_id = attempt_result.data[0]["id"]
        print(f"[SUBMIT] ✓ Created attempt: {attempt_id}")

        # Only update the task status to 'review' now that the attempt record exists.
        print(f"[SUBMIT] Setting task status to 'review'...")
        supabase.table("tasks").update({
            "status":     "review",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", request.task_id).execute()

        # Fetch internx_repo_url from project for main-codebase context
        internx_repo_url = ""
        project_id = task.get("project_id")
        if project_id:
            proj_result = supabase.table("projects")\
                .select("internx_repo_url")\
                .eq("id", project_id)\
                .single()\
                .execute()
            if proj_result.data:
                internx_repo_url = proj_result.data.get("internx_repo_url") or ""
                print(f"[SUBMIT] ✓ Project internx_repo_url: {internx_repo_url or '(not set)'}")

        # Queue background task — intern_role and internx_repo_url forwarded here
        print(f"[SUBMIT] Queueing background review task...")
        background_tasks.add_task(
            run_review_background,
            task_id=request.task_id,
            user_id=request.user_id.strip(),
            pr_url=request.pr_url.strip(),
            attempt_id=attempt_id,
            task_title=task.get("title", ""),
            task_description=task.get("description", ""),
            intern_role=intern_role,
            internx_repo_url=internx_repo_url,
            commit_sha=request.commit_sha or "",        # CHANGED: forwarded to background
            repo_full_name=request.repo_full_name or "", # CHANGED: forwarded to background
        )

        print(f"[SUBMIT] ✓ Review submission complete\n")

        return ReviewResponse(
            status="queued",
            attempt_id=attempt_id,
            message="Review queued. Please wait ~15 seconds."
        )

    except Exception as e:
        print(f"[SUBMIT] ❌ Error: {e}")
        print(traceback.format_exc())
        return ReviewResponse(
            status="error",
            message=f"Error: {str(e)}"
        )


# ── Design Review Route (/api/mentor/review/design) ─────────────────────────

@router.post("/review/design")
async def submit_design_review(
    request: DesignReviewRequest,
    background_tasks: BackgroundTasks
):
    """
    UI/UX role: accepts Figma URL + image upload + handoff checklist + explanation.
    No PR diff needed. Runs AI review against ui_ux rubric.
    """
    supabase = get_supabase()

    try:
        print(f"\n[DESIGN_REVIEW] task={request.task_id} user={request.user_id}")

        if not request.user_id or not request.user_id.strip():
            return ReviewResponse(status="error", message="user_id is required.")

        if not request.figma_url and not request.image_base64 and not request.explanation:
            return ReviewResponse(status="error", message="Please provide at least a Figma URL, screenshot, or explanation.")

        task_result = supabase.table("tasks")\
            .select("*").eq("id", request.task_id).single().execute()
        if not task_result.data:
            return ReviewResponse(status="error", message="Task not found")

        task = task_result.data
        # Design reviews always use ui_ux rubric
        intern_role = "ui_ux"

        attempt_result = supabase.table("review_attempts").insert({
            "task_id":    request.task_id,
            "user_id":    request.user_id.strip(),
            "pr_url":     request.figma_url or "(design submission — no PR)",
            "ai_model":   "llama-3.3-70b-versatile",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        if not attempt_result.data:
            return ReviewResponse(status="error", message="Failed to create review attempt")

        attempt_id = attempt_result.data[0]["id"]

        supabase.table("tasks").update({
            "status":     "review",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", request.task_id).execute()

        internx_repo_url = ""
        if task.get("project_id"):
            proj = supabase.table("projects").select("internx_repo_url")\
                .eq("id", task["project_id"]).single().execute()
            if proj.data:
                internx_repo_url = proj.data.get("internx_repo_url") or ""

        background_tasks.add_task(
            run_design_review_background,
            task_id=request.task_id,
            user_id=request.user_id.strip(),
            attempt_id=attempt_id,
            task_title=task.get("title", ""),
            task_description=task.get("description", ""),
            figma_url=request.figma_url or "",
            explanation=request.explanation,
            handoff_checklist=request.handoff_checklist,
            image_base64=request.image_base64,
            image_mime=request.image_mime or "image/png",
            internx_repo_url=internx_repo_url,
        )

        return ReviewResponse(
            status="queued",
            attempt_id=attempt_id,
            message="Design review queued. Please wait ~15 seconds."
        )

    except Exception as e:
        print(f"[DESIGN_REVIEW] ❌ {e}")
        print(traceback.format_exc())
        return ReviewResponse(status="error", message=f"Error: {str(e)}")


# ── QA Review Route (/api/mentor/review/qa) ──────────────────────────────────

@router.post("/review/qa")
async def submit_qa_review(
    request: QAReviewRequest,
    background_tasks: BackgroundTasks
):
    """
    Tester role: accepts bug report | test plan | automation PR.
    Routes to role-specific AI review with tester rubric.
    """
    supabase = get_supabase()

    try:
        print(f"\n[QA_REVIEW] task={request.task_id} type={request.submission_type}")

        if not request.user_id or not request.user_id.strip():
            return ReviewResponse(status="error", message="user_id is required.")

        valid_types = {"bug_report", "test_plan", "automation_pr"}
        if request.submission_type not in valid_types:
            return ReviewResponse(status="error", message=f"submission_type must be one of: {', '.join(valid_types)}")

        # Validate required fields per type
        if request.submission_type == "bug_report":
            if not request.bug_title or not request.bug_steps:
                return ReviewResponse(status="error", message="Bug report requires title and reproduction steps.")
        elif request.submission_type == "test_plan":
            if not request.test_plan_scope or not request.test_cases:
                return ReviewResponse(status="error", message="Test plan requires scope and test cases.")
        elif request.submission_type == "automation_pr":
            if not request.pr_url or "github.com" not in request.pr_url:
                return ReviewResponse(status="error", message="Automation PR requires a valid GitHub PR URL.")

        task_result = supabase.table("tasks")\
            .select("*").eq("id", request.task_id).single().execute()
        if not task_result.data:
            return ReviewResponse(status="error", message="Task not found")

        task = task_result.data
        pr_url_for_record = request.pr_url or f"(qa/{request.submission_type})"

        attempt_result = supabase.table("review_attempts").insert({
            "task_id":    request.task_id,
            "user_id":    request.user_id.strip(),
            "pr_url":     pr_url_for_record,
            "ai_model":   "llama-3.3-70b-versatile",
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        if not attempt_result.data:
            return ReviewResponse(status="error", message="Failed to create review attempt")

        attempt_id = attempt_result.data[0]["id"]

        supabase.table("tasks").update({
            "status":     "review",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", request.task_id).execute()

        internx_repo_url = ""
        if task.get("project_id"):
            proj = supabase.table("projects").select("internx_repo_url")\
                .eq("id", task["project_id"]).single().execute()
            if proj.data:
                internx_repo_url = proj.data.get("internx_repo_url") or ""

        background_tasks.add_task(
            run_qa_review_background,
            task_id=request.task_id,
            user_id=request.user_id.strip(),
            attempt_id=attempt_id,
            task_title=task.get("title", ""),
            task_description=task.get("description", ""),
            submission_type=request.submission_type,
            request=request,
            internx_repo_url=internx_repo_url,
        )

        return ReviewResponse(
            status="queued",
            attempt_id=attempt_id,
            message="QA review queued. Please wait ~15 seconds."
        )

    except Exception as e:
        print(f"[QA_REVIEW] ❌ {e}")
        print(traceback.format_exc())
        return ReviewResponse(status="error", message=f"Error: {str(e)}")


# ── Design Review Background Task ────────────────────────────────────────────

def run_design_review_background(
    task_id: str,
    user_id: str,
    attempt_id: str,
    task_title: str,
    task_description: str,
    figma_url: str,
    explanation: str,
    handoff_checklist: dict,
    image_base64: Optional[str],
    image_mime: str,
    internx_repo_url: str,
):
    from app.services.mentor import (
        review_design_professional, _build_error_review
    )
    supabase = get_supabase()
    print(f"\n[DESIGN_BG] Starting design review for task {task_id}")

    try:
        review_result = review_design_professional(
            task_id=task_id,
            task_title=task_title,
            task_description=task_description,
            figma_url=figma_url,
            explanation=explanation,
            handoff_checklist=handoff_checklist,
            image_base64=image_base64,
            image_mime=image_mime,
            internx_repo_url=internx_repo_url,
        )

        review_json_safe = json.loads(json.dumps(review_result, default=str))

        supabase.table("review_attempts").update({
            "score":       review_result.get("score"),
            "verdict":     review_result.get("verdict"),
            "confidence":  float(review_result.get("confidence", 0.5)),
            "review_json": review_json_safe,
        }).eq("id", attempt_id).execute()

        verdict = review_result.get("verdict")
        critical_blocks = [b for b in review_result.get("blocking_issues", []) if b.get("severity") == "critical"]
        new_status = "done" if verdict == "pass" and not critical_blocks else "in_progress"

        supabase.table("tasks").update({
            "status":     new_status,
            "score":      review_result.get("score"),
            "feedback":   json.dumps({"latest_review": review_result, "verdict": verdict}, default=str),
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", task_id).execute()

        # FIX: trigger adaptive engine when task is done via review path
        if new_status == "done":
            _fire_adaptive_on_done(user_id, task_id)

        print(f"[DESIGN_BG] ✅ Done: {verdict} ({review_result.get('score')}/100)")

    except Exception as e:
        print(f"[DESIGN_BG] ❌ {e}")
        print(traceback.format_exc())
        try:
            supabase.table("tasks").update({
                "status": "in_progress",
                "feedback": json.dumps({"error": str(e)}),
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", task_id).execute()
            supabase.table("review_attempts").update({
                "score":       None,
                "verdict":     "resubmit",
                "review_json": {"error": str(e)}
            }).eq("id", attempt_id).execute()
        except Exception:
            pass


# ── QA Review Background Task ─────────────────────────────────────────────────

def run_qa_review_background(
    task_id: str,
    user_id: str,
    attempt_id: str,
    task_title: str,
    task_description: str,
    submission_type: str,
    request,            # QAReviewRequest
    internx_repo_url: str,
):
    """
    Background task for QA review.
    After scoring, auto-raises a ticket + notifications when the AI flags
    the bug as ticketable and the score is credible.
    """
    from app.services.mentor import (
        review_qa_professional,
        create_ticket_from_bug_report,
        _build_error_review,
    )
    from app.core.database import get_supabase

    supabase = get_supabase()
    print(f"\n[QA_BG] Starting QA review — task={task_id} type={submission_type}")

    try:
        # ── Run AI review ─────────────────────────────────────────────────────
        review_result = review_qa_professional(
            task_id=task_id,
            task_title=task_title,
            task_description=task_description,
            submission_type=submission_type,
            request=request,
            internx_repo_url=internx_repo_url,
        )
        print(f"[QA_BG] ticket_meta = {review_result.get('ticket_meta')}")

        # ── Auto-raise ticket if bug_report and AI says ticketable ───────────
        ticket_raised: Optional[dict] = None

        if submission_type == "bug_report":
            ticket_meta = review_result.get("ticket_meta") or {}
            if ticket_meta.get("is_ticketable"):
                print(f"[QA_BG] Bug is ticketable — fetching task for project/group info...")

                task_row = supabase.table("tasks") \
                    .select("project_id, group_id") \
                    .eq("id", task_id) \
                    .single() \
                    .execute()

                project_id = (task_row.data or {}).get("project_id")
                group_id   = (task_row.data or {}).get("group_id")

                if project_id:
                    ticket_result = create_ticket_from_bug_report(
                        supabase,
                        task_id=task_id,
                        project_id=project_id,
                        group_id=group_id,
                        created_by=user_id,
                        ticket_title=ticket_meta.get(
                            "ticket_title",
                            request.bug_title or task_title,
                        ),
                        ticket_description=ticket_meta.get("ticket_description", ""),
                        ticket_priority=ticket_meta.get("ticket_priority", "medium"),
                        affected_roles=ticket_meta.get("affected_roles", []),
                        bug_severity=getattr(request, "bug_severity", None),
                    )

                    ticket_raised = {
                        "ticket_id":      ticket_result.get("ticket_id"),
                        "notified_users": ticket_result.get("notified_users", []),
                        "notified_count": len(ticket_result.get("notified_users", [])),
                        "affected_roles": ticket_meta.get("affected_roles", []),
                        "ticket_priority": ticket_meta.get("ticket_priority", "medium"),
                        "ticket_title":   ticket_meta.get("ticket_title", ""),
                        "error":          ticket_result.get("error"),
                    }

                    if ticket_result.get("error"):
                        print(f"[QA_BG] ⚠️  Ticket creation error: {ticket_result['error']}")
                    else:
                        print(
                            f"[QA_BG] ✓ Ticket raised: {ticket_result['ticket_id']} | "
                            f"notified: {len(ticket_result['notified_users'])} user(s)"
                        )
                else:
                    print("[QA_BG] ⚠️  No project_id on task — skipping ticket creation")

        # Attach ticket info to result so the frontend can show a banner
        if ticket_raised:
            review_result["ticket_raised"] = ticket_raised

        # ── Persist to DB ─────────────────────────────────────────────────────
        review_json_safe = json.loads(json.dumps(review_result, default=str))

        supabase.table("review_attempts").update({
            "score":       review_result.get("score"),
            "verdict":     review_result.get("verdict"),
            "confidence":  float(review_result.get("confidence", 0.5)),
            "review_json": review_json_safe,
        }).eq("id", attempt_id).execute()

        verdict         = review_result.get("verdict")
        critical_blocks = [
            b for b in review_result.get("blocking_issues", [])
            if b.get("severity") == "critical"
        ]
        new_status = "done" if verdict == "pass" and not critical_blocks else "in_progress"

        supabase.table("tasks").update({
            "status":     new_status,
            "score":      review_result.get("score"),
            "feedback":   json.dumps(
                {"latest_review": review_result, "verdict": verdict},
                default=str,
            ),
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", task_id).execute()

        # FIX: trigger adaptive engine when task is done via review path
        if new_status == "done":
            _fire_adaptive_on_done(user_id, task_id)

        print(f"[QA_BG] ✅ Done: {verdict} ({review_result.get('score')}/100)")

    except Exception as e:
        print(f"[QA_BG] ❌ {e}")
        print(traceback.format_exc())
        try:
            supabase.table("tasks").update({
                "status":     "in_progress",
                "feedback":   json.dumps({"error": str(e)}),
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", task_id).execute()

            supabase.table("review_attempts").update({
                "score":       None,
                "verdict":     "resubmit",
                "review_json": {"error": str(e)},
            }).eq("id", attempt_id).execute()
        except Exception:
            pass


@router.get("/review/history/{task_id}")
async def get_review_history(task_id: str):
    """
    Get all review attempts for a task.
    """
    supabase = get_supabase()

    try:
        print(f"[HISTORY] Fetching history for task {task_id}")

        result = supabase.table("review_attempts")\
            .select("*")\
            .eq("task_id", task_id)\
            .order("created_at", desc=True)\
            .execute()

        print(f"[HISTORY] ✓ Found {len(result.data or [])} attempts")

        return {
            "attempts": result.data or [],
            "count": len(result.data or [])
        }

    except Exception as e:
        print(f"[HISTORY] ❌ Error: {e}")
        return {
            "attempts": [],
            "count": 0,
            "error": str(e)
        }


@router.get("/review/attempt/{attempt_id}")
async def get_review_attempt(attempt_id: str):
    """
    Get a specific review attempt.
    """
    supabase = get_supabase()

    try:
        result = supabase.table("review_attempts")\
            .select("*")\
            .eq("id", attempt_id)\
            .single()\
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Review attempt not found")

        return result.data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Background Task ─────────────────────────────────────────────────────────

def _fire_notification(supabase, user_id: str, key: str, ntype: str,
                        title: str, body: str, icon: str, href: str):
    """Helper — insert a notification row, swallowing errors so a notification
    failure never kills the review pipeline."""
    try:
        supabase.table("notifications").insert({
            "user_id":    user_id,
            "key":        key,
            "type":       ntype,
            "title":      title,
            "body":       body,
            "icon":       icon,
            "href":       href,
            "count":      1,
            "is_read":    False,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as notify_err:
        print(f"[NOTIFY] ⚠️  Failed to send notification: {notify_err}")


# CHANGED: Added commit_sha and repo_full_name params; now calls run_review_pipeline
def run_review_background(
    task_id, user_id, pr_url, attempt_id,
    task_title, task_description,
    intern_role="default", internx_repo_url="",
    commit_sha="", repo_full_name=""
):
    import asyncio
    from app.services.github_service import check_pr_mergeable, merge_pr_squash
    supabase = get_supabase()

    try:
        # CHANGED: call run_review_pipeline instead of review_pr_professional
        # run_review_pipeline is async so we run it in a new event loop
        review_result = asyncio.run(run_review_pipeline(
            task_id=task_id,
            pr_url=pr_url,
            task_title=task_title,
            task_description=task_description,
            intern_role=intern_role,
            internx_repo_url=internx_repo_url,
            commit_sha=commit_sha,
            repo_full_name=repo_full_name,
        ))

        # score/verdict live inside layer3 — pipeline promotes them to top-level
        # but fall back to layer3 explicitly in case an older pipeline version is used
        l3_data  = review_result.get("layer3") or {}
        verdict  = review_result.get("verdict") or l3_data.get("verdict")
        raw_score = review_result.get("score") or l3_data.get("score")
        # Also try recomputing from breakdown if score is still missing/zero
        if not raw_score:
            bd = review_result.get("breakdown") or l3_data.get("breakdown") or {}
            raw_score = sum(v for v in bd.values() if isinstance(v, (int, float))) or None
        score    = int(round(raw_score)) if raw_score else 0
        blocking = l3_data.get("blocking_issues") or review_result.get("blocking_issues", [])

        # ── FIX 1: Only block on CRITICAL severity, not all blocking issues ──
        critical_blocks = [
            b for b in blocking
            if str(b.get("severity", "")).lower() == "critical"
        ]

        # CHANGED: write all layer results to review_attempts
        review_json_safe = json.loads(json.dumps(review_result, default=str))
        update_payload = {
            "score":       score,
            "verdict":     verdict,
            "confidence":  float(review_result.get("confidence") or l3_data.get("confidence", 0.5)),
            "review_json": review_json_safe,
        }

        # Write layer results if pipeline returned them
        if "layer1" in review_result:
            update_payload.update({
                "layer1_verdict":    review_result["layer1"].get("verdict"),
                "layer1_ci":         review_result["layer1"].get("ci"),
                "layer1_security":   review_result["layer1"].get("security"),
                "layer1_error_logs": review_result["layer1"].get("error_logs"),
            })

        # ── Step 6: Persist Layer 2 result immediately after it resolves ──────
        # run_review_pipeline returns the full result including layer2; we save
        # those fields here so polling clients can see layer2 data before the
        # full review_json is written (and so layer2 is always persisted even if
        # a later stage errors out).
        if review_result.get("layer2"):
            l2 = review_result["layer2"]
            try:
                supabase.table("review_attempts").update({
                    "layer2_risk_score": l2.get("risk_score"),
                    "layer2_pr_type":    l2.get("pr_type"),
                    "layer2_complexity": l2.get("complexity"),
                    "status":            "layer2_complete",
                }).eq("id", attempt_id).execute()
                print(
                    f"[BG_TASK] ✓ Layer 2 saved — risk={l2.get('risk_score')} "
                    f"type={l2.get('pr_type')} complexity={l2.get('complexity')}"
                )
            except Exception as l2_save_err:
                # Non-fatal — log and continue; the full payload write below will
                # still include layer2 data via review_json.
                print(f"[BG_TASK] ⚠️  Layer 2 intermediate save failed: {l2_save_err}")

            update_payload.update({
                "layer2_risk_score": l2.get("risk_score"),
                "layer2_pr_type":    l2.get("pr_type"),
                "layer2_complexity": l2.get("complexity"),
            })

        if review_result.get("layer3"):
            update_payload["layer3_result"] = review_result["layer3"]

        # Handle pipeline blocked at Layer 1
        pipeline_verdict = review_result.get("pipeline_verdict", "")
        if pipeline_verdict == "blocked_layer1":
            update_payload.update({
                "layer1_verdict":    "block",
                "layer1_error_logs": review_result.get("layer1", {}).get("error_logs"),
                "status":            "blocked",
                "verdict":           "blocked",
            })
            supabase.table("review_attempts").update(update_payload).eq("id", attempt_id).execute()
            supabase.table("tasks").update({
                "status":     "in_progress",
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", task_id).execute()
            print(f"[BG_TASK] ⛔ Blocked at Layer 1 — not firing adaptive engine")
            return

        supabase.table("review_attempts").update(update_payload).eq("id", attempt_id).execute()

        # ── FIX 2: Trust the verdict ──
        should_merge = (verdict == "pass") and (not critical_blocks)

        merge_status = "pending"
        merge_error  = ""
        merge_sha    = ""
        new_status   = "in_progress"

        if should_merge and pr_url and "github.com" in pr_url:
            print(f"[BG_TASK] Verdict=pass, score={score} — checking mergeability")
            try:
                mergeable = check_pr_mergeable(pr_url)
            except Exception as merge_check_err:
                print(f"[BG_TASK] ⚠️ check_pr_mergeable threw: {merge_check_err} — marking done anyway")
                mergeable = None

            if mergeable is True:
                try:
                    merge_result = merge_pr_squash(pr_url, task_title)
                except Exception as merge_err:
                    print(f"[BG_TASK] ⚠️ merge_pr_squash threw: {merge_err}")
                    merge_result = {"success": False, "message": str(merge_err)}

                if merge_result["success"]:
                    merge_status = "merged"
                    merge_sha    = merge_result.get("sha", "")
                    new_status   = "done"
                    print(f"[BG_TASK] ✅ Squash-merged successfully")
                    _fire_notification(
                        supabase, user_id,
                        key=f"merged:{task_id}",
                        ntype="task_complete",
                        title="✅ Task merged and complete!",
                        body=f"'{task_title}' was merged into your team repo.",
                        icon="🎉",
                        href=f"/internship/tasks/{task_id}",
                    )
                else:
                    merge_status = "skipped"
                    merge_error  = merge_result.get("message", "Merge failed")
                    new_status   = "done"
                    print(f"[BG_TASK] ⚠️ Merge failed ({merge_error}) — marking done anyway")

            elif mergeable is False:
                merge_status = "conflict"
                merge_error  = (
                    "Merge conflict detected. Pull the base branch, "
                    "resolve conflicts, push, then resubmit."
                )
                new_status = "in_progress"
                print(f"[BG_TASK] ❌ Conflict — keeping in_progress")
                _fire_notification(
                    supabase, user_id,
                    key=f"conflict:{task_id}",
                    ntype="conflict",
                    title="🔀 Merge conflicts in your PR",
                    body=f"'{task_title}' passed (score {score}/100) but has conflicts. Resolve and resubmit.",
                    icon="🔀",
                    href=f"/internship/tasks/{task_id}",
                )

            else:
                # None — GitHub API unreachable; mark done, log warning
                merge_status = "skipped"
                new_status   = "done"
                print(f"[BG_TASK] ⚠️ Could not check mergeability — marking done")

        elif should_merge:
            # Pass verdict but no GitHub PR URL
            merge_status = "skipped"
            new_status   = "done"
            print(f"[BG_TASK] No GitHub PR URL — marking done without merge")

        # ── Persist ───────────────────────────────────────────────────────
        feedback_data = {
            "latest_review": review_result,
            "verdict":       verdict,
            "score":         score,
            "intern_role":   intern_role,
            "merge_status":  merge_status,
            "merge_error":   merge_error,
            "merge_sha":     merge_sha,
            "updated_at":    datetime.utcnow().isoformat(),
        }

        supabase.table("tasks").update({
            "status":     new_status,
            "score":      score,
            "feedback":   json.dumps(feedback_data, default=str),
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", task_id).execute()

        # FIX: trigger adaptive engine when task is done via review path
        if new_status == "done":
            _fire_adaptive_on_done(user_id, task_id)

        print(f"[BG_TASK] ✅ Done: status={new_status} merge={merge_status}\n")

    except Exception as e:
        print(f"[BG_TASK] ❌ {e}")
        print(traceback.format_exc())
        try:
            supabase.table("tasks").update({
                "status":     "in_progress",
                "feedback":   json.dumps({"error": str(e), "error_at": datetime.utcnow().isoformat()}),
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", task_id).execute()
            supabase.table("review_attempts").update({
                "score":       None,
                "verdict":     "resubmit",
                "review_json": {"error": str(e), "error_type": type(e).__name__},
            }).eq("id", attempt_id).execute()
        except Exception:
            pass


# ── Phase 6: Merge-retry endpoint ────────────────────────────────────────────

class MergeRetryRequest(BaseModel):
    task_id: str
    pr_url: str
    user_id: str


@router.post("/review/merge-retry")
async def merge_retry(request: MergeRetryRequest):
    """
    Called when an intern resubmits after a merge conflict.
    Skips AI review — only re-checks mergeability and squash-merges if clean.

    Returns:
      { "status": "merged" | "conflict" | "error", "message": "..." }
    """
    from app.services.github_service import check_pr_mergeable, merge_pr_squash

    supabase = get_supabase()

    print(f"\n[MERGE_RETRY] task={request.task_id} user={request.user_id}")

    try:
        # Fetch the task to get title and validate
        task_result = supabase.table("tasks") \
            .select("*") \
            .eq("id", request.task_id) \
            .single() \
            .execute()

        if not task_result.data:
            raise HTTPException(404, "Task not found")

        task       = task_result.data
        task_title = task.get("title", "")
        pr_url     = request.pr_url.strip()

        if not pr_url or "github.com" not in pr_url:
            raise HTTPException(400, "Valid GitHub PR URL is required")

        # Re-check mergeability
        mergeable = check_pr_mergeable(pr_url)

        if mergeable is False:
            # Still conflicted
            _fire_notification(
                supabase, request.user_id,
                key=f"conflict:{request.task_id}",
                ntype="conflict",
                title="🔀 Still has merge conflicts",
                body=f"'{task_title}' still has conflicts. Resolve them in your branch and resubmit.",
                icon="🔀",
                href=f"/internship/tasks/{request.task_id}",
            )
            return {"status": "conflict", "message": "PR still has merge conflicts. Resolve them and resubmit."}

        if mergeable is None:
            # Can't determine — mark done with a warning
            print(f"[MERGE_RETRY] ⚠️  GitHub API unreachable — marking done")
            supabase.table("tasks").update({
                "status":     "done",
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("id", request.task_id).execute()
            # FIX: trigger adaptive engine
            _fire_adaptive_on_done(request.user_id, request.task_id)
            return {"status": "merged", "message": "Could not verify merge status — task marked complete."}

        # Mergeable! Squash-merge it
        merge_result = merge_pr_squash(pr_url, task_title)

        if merge_result["success"]:
            # Fetch existing feedback to preserve review data
            existing_feedback: dict = {}
            try:
                raw = task.get("feedback") or "{}"
                existing_feedback = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                pass

            existing_feedback.update({
                "merge_status": "merged",
                "merge_error":  "",
                "merge_sha":    merge_result.get("sha", ""),
                "updated_at":   datetime.utcnow().isoformat(),
            })

            supabase.table("tasks").update({
                "status":        "done",
                "github_pr_url": pr_url,
                "feedback":      json.dumps(existing_feedback, default=str),
                "updated_at":    datetime.utcnow().isoformat(),
            }).eq("id", request.task_id).execute()

            # FIX: trigger adaptive engine
            _fire_adaptive_on_done(request.user_id, request.task_id)

            _fire_notification(
                supabase, request.user_id,
                key=f"merged:{request.task_id}",
                ntype="task_complete",
                title="✅ Task merged and complete!",
                body=f"'{task_title}' was merged into your team repo. Check your team's GitHub!",
                icon="🎉",
                href=f"/internship/tasks/{request.task_id}",
            )
            return {"status": "merged", "message": "PR merged successfully. Task is now complete!"}

        else:
            return {
                "status":  "error",
                "message": merge_result.get("message", "Merge failed — try again shortly."),
            }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[MERGE_RETRY] ❌ {e}")
        print(traceback.format_exc())
        raise HTTPException(500, f"Merge retry failed: {str(e)}")


# ── Chatbot Endpoints ────────────────────────────────────────────────────────

class ProjectChatRequest(BaseModel):
    message: str
    user_id: str
    project_context: str = ""


@router.post("/project-chat")
async def project_chat(body: ProjectChatRequest):
    """REST endpoint for project-level mentor chat (no task ID needed)."""
    from groq import Groq
    from app.core.config import get_settings

    settings = get_settings()
    ai_client = Groq(api_key=settings.groq_api_key)

    system_prompt = f"""You are an AI mentor for a software engineering intern on InternX.

The intern is working on this project:
{body.project_context}

Help them understand the project, plan their work, answer technical questions, and guide them through their internship.
Be specific to this project context. Keep answers concise and practical."""

    response = ai_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": body.message}
        ],
    )
    return {"reply": response.choices[0].message.content}


@router.websocket("/chat/{task_id}")
async def mentor_chat(task_id: str, websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("message", "")
            user_id = data.get("user_id", "")

            if not user_message or not user_id:
                await websocket.send_text("[ERROR] Missing message or user_id")
                continue

            from app.services.mentor_chat import stream_mentor_response
            async for token in stream_mentor_response(
                task_id=task_id,
                user_id=user_id,
                user_message=user_message,
            ):
                await websocket.send_text(token)

            await websocket.send_text("[DONE]")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"[ERROR] {str(e)}")
        await websocket.close()


@router.get("/sessions/{task_id}")
async def get_chat_history(task_id: str):
    from app.services.mentor_chat import get_session_history
    supabase = get_supabase()
    return {"task_id": task_id, "messages": []}


@router.get("/summary/{user_id}")
async def get_learning_summary(user_id: str):
    from groq import Groq
    from app.core.config import get_settings

    settings = get_settings()
    ai_client = Groq(api_key=settings.groq_api_key)
    supabase = get_supabase()

    tasks_result = (
        supabase.table("tasks")
        .select("title, description, score, feedback")
        .eq("assigned_to", user_id)
        .eq("status", "done")
        .execute()
    )
    tasks = tasks_result.data or []

    if not tasks:
        return {"summary": "No completed tasks yet."}

    task_list = "\n".join(
        f"- {t['title']} (score: {t.get('score', 'N/A')}): {t.get('feedback', '')}"
        for t in tasks
    )

    prompt = f"""
A software engineering intern has completed these tasks:
{task_list}

Write a 3-paragraph professional learning summary for their portfolio.
Highlight skills demonstrated, improvement over time, and readiness for real internships.
Keep it encouraging and specific.
""".strip()

    response = ai_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
    )
    return {"summary": response.choices[0].message.content}

# ── Phase 4: Progressive review status polling ────────────────────────────────

@router.get("/review-status/{attempt_id}")
async def get_review_status(attempt_id: str):
    """
    Phase 4 — polling endpoint for progressive UI.

    Returns partial layer results as they complete so the frontend can
    show each layer's status without waiting for the full Groq response.

    Response shape:
    {
        "attempt_id":    str,
        "status":        "pending" | "layer1_complete" | "layer2_complete"
                         | "complete" | "blocked" | "error",
        "pipeline_verdict": str | None,
        "layer1": {
            "verdict":      "pass" | "block" | None,
            "ci":           dict | None,
            "security":     list | None,
            "error_logs":   list | None,
        } | None,
        "layer2": {
            "risk_score":  float | None,
            "pr_type":     str | None,
            "complexity":  str | None,
            "display":     dict | None,
        } | None,
        "layer3": {
            "verdict":         str | None,
            "score":           int | None,
            "review_summary":  str | None,
            "skip_if_passing": bool,
        } | None,
        "score":   int | None,
        "verdict": str | None,
    }
    """
    supabase = get_supabase()

    try:
        row = supabase.table("review_attempts") \
            .select("*") \
            .eq("id", attempt_id) \
            .single() \
            .execute()

        if not row.data:
            raise HTTPException(status_code=404, detail="Attempt not found")

        data = row.data
        rj   = data.get("review_json") or {}
        if isinstance(rj, str):
            import json as _json
            try:
                rj = _json.loads(rj)
            except Exception:
                rj = {}

        # ── Determine status from DB columns ─────────────────────────────────
        db_status = data.get("status") or "pending"

        # Layer 1
        layer1_out = None
        l1v = data.get("layer1_verdict")
        if l1v:
            layer1_out = {
                "verdict":    l1v,
                "ci":         data.get("layer1_ci"),
                "security":   data.get("layer1_security"),
                "error_logs": data.get("layer1_error_logs"),
            }
        elif rj.get("layer1"):
            l1 = rj["layer1"]
            layer1_out = {
                "verdict":    l1.get("verdict"),
                "ci":         l1.get("ci"),
                "security":   l1.get("security"),
                "error_logs": l1.get("error_logs"),
            }

        # Layer 2
        layer2_out = None
        l2_score = data.get("layer2_risk_score")
        l2_type  = data.get("layer2_pr_type")
        l2_cmplx = data.get("layer2_complexity")
        if any(v is not None for v in [l2_score, l2_type, l2_cmplx]):
            risk_label = (
                "high"   if (l2_score or 0) >= 0.65 else
                "medium" if (l2_score or 0) >= 0.35 else
                "low"
            )
            layer2_out = {
                "risk_score":  l2_score,
                "risk_label":  risk_label,
                "pr_type":     l2_type,
                "complexity":  l2_cmplx,
                "display": {
                    "badge":      f"Risk Score: {int((l2_score or 0.5) * 100)}% ({risk_label.title()})",
                    "pr_type":    f"PR Type: {(l2_type or 'Unknown').title()}",
                    "complexity": f"Review Complexity: {(l2_cmplx or 'Medium').title()}",
                },
            }
        elif rj.get("layer2"):
            l2 = rj["layer2"]
            layer2_out = {
                "risk_score":  l2.get("risk_score"),
                "risk_label":  l2.get("risk_label"),
                "pr_type":     l2.get("pr_type"),
                "complexity":  l2.get("complexity"),
                "display":     l2.get("display"),
            }

        # Layer 3 / final result
        layer3_out = None
        l3_data = data.get("layer3_result") or rj.get("layer3")
        if l3_data and isinstance(l3_data, dict) and l3_data.get("verdict"):
            layer3_out = {
                "verdict":         l3_data.get("verdict"),
                "score":           l3_data.get("score"),
                "review_summary":  l3_data.get("review_summary"),
                "skip_if_passing": l3_data.get("layer3_skip_if_passing", False),
            }
        elif data.get("verdict") and db_status == "complete":
            layer3_out = {
                "verdict":         data.get("verdict"),
                "score":           data.get("score"),
                "review_summary":  rj.get("review_summary"),
                "skip_if_passing": rj.get("layer3_skip_if_passing", False),
            }

        # Derive a clean status if the DB hasn't been updated with named phases
        inferred_status = db_status
        if db_status not in ("pending", "layer1_complete", "layer2_complete",
                              "complete", "blocked", "error"):
            if data.get("score") is not None:
                inferred_status = "complete"
            elif layer2_out:
                inferred_status = "layer2_complete"
            elif layer1_out:
                inferred_status = "layer1_complete"
            else:
                inferred_status = "pending"

        return {
            "attempt_id":       attempt_id,
            "status":           inferred_status,
            "pipeline_verdict": rj.get("pipeline_verdict") or data.get("verdict"),
            "layer1":           layer1_out,
            "layer2":           layer2_out,
            "layer3":           layer3_out,
            "score":            data.get("score"),
            "verdict":          data.get("verdict"),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[REVIEW_STATUS] ❌ {e}")
        raise HTTPException(status_code=500, detail=str(e))