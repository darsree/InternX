# backend/app/routers/incidents.py
from fastapi import APIRouter, HTTPException, Depends
from app.core.auth import get_current_user
from app.core.database import get_supabase
from datetime import datetime, timezone, timedelta

router = APIRouter(prefix="/api/incidents", tags=["Incidents"])

PROJECT_ID = "aaaaaaaa-0000-0000-0000-000000000001"
GROUP_ID   = "bbbbbbbb-0000-0000-0000-000000000001"
SPRINT_ID  = "ffffffff-be00-0000-0000-000000000001"

INCIDENT_TITLE = "SEV-1: Race condition in POST /api/orders — duplicate orders & negative stock detected"
HOTFIX_ROLES   = {"backend", "frontend", "tester"}


def _notify(supabase, project_id, key, title, body, icon, href="/dashboard"):
    members = supabase.table("user_projects").select("user_id").eq("project_id", project_id).execute()
    if not members.data:
        return
    notifications = [
        {
            "user_id": m["user_id"], "key": key, "type": "incident",
            "title": title, "body": body, "icon": icon, "href": href, "count": 1,
        }
        for m in members.data
    ]
    supabase.table("notifications").upsert(notifications, on_conflict="user_id,key").execute()


def _restore_paused_tasks(supabase, incident_id, skip_hotfix=True):
    """Restore all paused sprint tasks to their previous status."""
    paused = (
        supabase.table("tasks")
        .select("id,previous_status,title")
        .eq("incident_id", incident_id)
        .eq("status", "paused")
        .execute()
    )
    for task in (paused.data or []):
        if skip_hotfix and task.get("title", "").startswith("[HOTFIX]"):
            continue
        supabase.table("tasks").update({
            "status":          task.get("previous_status") or "todo",
            "previous_status": None,
        }).eq("id", task["id"]).execute()


def _get_role_member_map(supabase, project_id, group_id):
    """
    Returns a dict mapping intern_role → user_id for the project team.
    Looks up profiles joined to this project (optionally filtered by group).
    Falls back to any member with that role if group lookup fails.
    """
    role_to_user = {}

    # First try group_members table (more specific)
    if group_id:
        group_res = (
            supabase.table("group_members")
            .select("user_id,intern_role")
            .eq("group_id", group_id)
            .execute()
        )
        for m in (group_res.data or []):
            role = m.get("intern_role")
            if role and role not in role_to_user:
                role_to_user[role] = m["user_id"]

    # Fill missing roles from profiles joined to project
    missing_roles = HOTFIX_ROLES - set(role_to_user.keys())
    if missing_roles:
        profiles_res = (
            supabase.table("profiles")
            .select("id,intern_role")
            .eq("project_id", project_id)
            .in_("intern_role", list(missing_roles))
            .execute()
        )
        for p in (profiles_res.data or []):
            role = p.get("intern_role")
            if role and role not in role_to_user:
                role_to_user[role] = p["id"]

    return role_to_user


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/incidents/trigger
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/trigger")
async def trigger_incident(body: dict, user=Depends(get_current_user), supabase=Depends(get_supabase)):
    incident_id = body.get("incident_id")
    project_id  = body.get("project_id", PROJECT_ID)
    group_id    = body.get("group_id",   GROUP_ID)
    sprint_id   = body.get("sprint_id",  SPRINT_ID)

    if not incident_id:
        raise HTTPException(status_code=400, detail="incident_id required")

    deadline = (datetime.now(timezone.utc) + timedelta(minutes=90)).isoformat()
    now      = datetime.now(timezone.utc).isoformat()

    row = {
        "title":           INCIDENT_TITLE,
        "status":          "active",
        "project_id":      project_id,
        "group_id":        group_id,
        "sprint_id":       sprint_id,
        "triggered_by":    user["id"],
        "sla_deadline":    deadline,
        "sla_minutes":     90,
        "created_at":      now,
        "resolved_at":     None,
        "elapsed_seconds": None,
        "postmortem_json": None,
    }

    update = supabase.table("incidents").update(row).eq("id", incident_id).execute()
    incident = update.data[0] if update.data else None
    if not incident:
        ins = supabase.table("incidents").insert({"id": incident_id, **row}).execute()
        incident = ins.data[0] if ins.data else None
    if not incident:
        raise HTTPException(status_code=500, detail="Failed to activate incident")

    # Pause all non-done sprint tasks (not already paused)
    tasks = (
        supabase.table("tasks").select("id,status")
        .eq("sprint_id", sprint_id)
        .neq("status", "done")
        .neq("status", "paused")
        .execute()
    )
    for t in (tasks.data or []):
        supabase.table("tasks").update({
            "previous_status": t["status"],
            "status":          "paused",
            "incident_id":     incident["id"],
        }).eq("id", t["id"]).execute()

    # Remove any stale hotfix tasks from a prior trigger
    supabase.table("tasks").delete() \
        .eq("incident_id", incident["id"]) \
        .like("title", "[HOTFIX]%").execute()

    # Resolve which user to assign each hotfix role to
    role_member_map = _get_role_member_map(supabase, project_id, group_id)

    # Build the 3 hotfix task definitions
    hotfix_definitions = [
        {
            "sprint_id":   sprint_id,
            "project_id":  project_id,
            "incident_id": incident["id"],
            "group_id":    group_id,
            "title":       "[HOTFIX] Fix race condition in stock decrement — atomic DB lock",
            "description": (
                "The POST /api/orders endpoint is not atomic. Two concurrent requests can both "
                "pass the stock check before either decrements.\n\n"
                "Fix: wrap the stock check and decrement in a single atomic UPDATE ... WHERE "
                "stock_quantity >= qty, then check affected rows. If 0 rows updated → 409.\n\n"
                "Verify with asyncio.gather of 10 simultaneous POST /api/orders calls for the "
                "same product with qty=1 and stock_quantity=1 — only 1 should succeed."
            ),
            "intern_role": "backend",
            "status":      "todo",
            "priority":    "high",
            "difficulty":  "hard",
            "assigned_to": role_member_map.get("backend"),
        },
        {
            "sprint_id":   sprint_id,
            "project_id":  project_id,
            "incident_id": incident["id"],
            "group_id":    group_id,
            "title":       "[HOTFIX] Disable checkout button during in-flight order submission",
            "description": (
                "The checkout form allows users to click 'Place Order' multiple times while the "
                "POST /api/orders request is in-flight, triggering duplicate submissions.\n\n"
                "Fix:\n"
                "1. Set a submitting boolean state on click; disable button + show spinner.\n"
                "2. Generate a UUID idempotency key in sessionStorage, send as X-Idempotency-Key. "
                "Show a 'Duplicate order prevented' toast if backend returns 409."
            ),
            "intern_role": "frontend",
            "status":      "todo",
            "priority":    "high",
            "difficulty":  "medium",
            "assigned_to": role_member_map.get("frontend"),
        },
        {
            "sprint_id":   sprint_id,
            "project_id":  project_id,
            "incident_id": incident["id"],
            "group_id":    group_id,
            "title":       "[HOTFIX] Verify no negative stock_quantity in DB + concurrent checkout test",
            "description": (
                "1. Run: SELECT id, name, stock_quantity FROM products WHERE stock_quantity < 0;\n"
                "   Document any rows as a GitHub issue (severity=critical).\n\n"
                "2. Write a Playwright test: two browser tabs simultaneously add the same "
                "last-in-stock item to cart and attempt checkout. Assert only one succeeds "
                "and the other gets a clear out-of-stock error.\n\n"
                "Submit findings + test as your hotfix PR."
            ),
            "intern_role": "tester",
            "status":      "todo",
            "priority":    "high",
            "difficulty":  "medium",
            "assigned_to": role_member_map.get("tester"),
        },
    ]

    # Insert hotfix tasks — filter out None assigned_to if column is NOT NULL
    # (our schema allows NULL for assigned_to, so we leave it as None if no match)
    supabase.table("tasks").insert(hotfix_definitions).execute()

    _notify(supabase, project_id,
            key=f"incident_{incident['id']}",
            title="🚨 SEV-1 Incident Declared — Sprint Paused",
            body=incident["title"], icon="🚨")

    return {"incident": incident, "role_assignments": {k: bool(v) for k, v in role_member_map.items()}}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/incidents/active
# Returns hotfix-only tasks in the join (filters out paused sprint tasks).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/active")
async def get_active_incident(project_id: str, supabase=Depends(get_supabase)):
    result = (
        supabase.table("incidents")
        .select("*, tasks(id,title,intern_role,status,github_pr_url,incident_id,assigned_to)")
        .eq("project_id", project_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"incident": None}

    incident = result.data[0]
    # Strip paused sprint tasks — only expose [HOTFIX] tasks to the panel
    incident["tasks"] = [
        t for t in (incident.get("tasks") or [])
        if t.get("title", "").startswith("[HOTFIX]")
    ]

    deadline = datetime.fromisoformat(incident["sla_deadline"].replace("Z", "+00:00"))
    seconds_remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
    return {"incident": {**incident, "seconds_remaining": seconds_remaining}}


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/incidents/{incident_id}/resolve
# Normal resolve: restores sprint tasks, keeps hotfix tasks as a record.
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{incident_id}/resolve")
async def resolve_incident(incident_id: str, supabase=Depends(get_supabase)):
    result = supabase.table("incidents").select("*").eq("id", incident_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = result.data[0]
    if incident["status"] == "resolved":
        raise HTTPException(status_code=400, detail="Already resolved")

    resolved_at = datetime.now(timezone.utc)
    created_at  = datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00"))
    elapsed     = int((resolved_at - created_at).total_seconds())

    supabase.table("incidents").update({
        "status":          "resolved",
        "resolved_at":     resolved_at.isoformat(),
        "elapsed_seconds": elapsed,
    }).eq("id", incident_id).execute()

    _restore_paused_tasks(supabase, incident_id, skip_hotfix=True)

    mins, secs = divmod(elapsed, 60)
    _notify(supabase, incident["project_id"],
            key=f"incident_resolved_{incident_id}",
            title="✅ Incident Resolved — Sprint Resumed",
            body=f"{incident['title']} — resolved in {mins}m {secs}s",
            icon="✅")

    return {"ok": True, "elapsed_seconds": elapsed}


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/incidents/{incident_id}/end-mode
# Hard stop: resolves incident AND deletes all hotfix tasks.
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{incident_id}/end-mode")
async def end_incident_mode(incident_id: str, user=Depends(get_current_user), supabase=Depends(get_supabase)):
    result = supabase.table("incidents").select("*").eq("id", incident_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = result.data[0]
    if incident["status"] == "resolved":
        raise HTTPException(status_code=400, detail="Already resolved")

    resolved_at = datetime.now(timezone.utc)
    created_at  = datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00"))
    elapsed     = int((resolved_at - created_at).total_seconds())

    supabase.table("incidents").update({
        "status":          "resolved",
        "resolved_at":     resolved_at.isoformat(),
        "elapsed_seconds": elapsed,
    }).eq("id", incident_id).execute()

    # Restore paused sprint tasks (including restoring hotfix-tagged ones if any were paused)
    _restore_paused_tasks(supabase, incident_id, skip_hotfix=False)

    # Delete ALL hotfix tasks — clean slate
    supabase.table("tasks").delete() \
        .eq("incident_id", incident_id) \
        .like("title", "[HOTFIX]%").execute()

    _notify(supabase, incident["project_id"],
            key=f"incident_ended_{incident_id}",
            title="🛑 Incident Mode Ended — Sprint Resumed",
            body="The production incident simulation has been stopped. Your sprint tasks are active again.",
            icon="🛑")

    return {"ok": True, "elapsed_seconds": elapsed}


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/incidents/{incident_id}/hotfix-summary
# Member submits their postmortem paragraph for their hotfix task.
# Stored in the task's `feedback` column as JSON:
#   { "hotfix_summary": "...", "submitted_at": "..." }
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{incident_id}/hotfix-summary")
async def submit_hotfix_summary(
    incident_id: str,
    body: dict,
    user=Depends(get_current_user),
    supabase=Depends(get_supabase),
):
    import json as _json

    task_id = body.get("task_id")
    summary = (body.get("summary") or "").strip()

    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")
    if not summary:
        raise HTTPException(status_code=400, detail="summary cannot be empty")
    if len(summary) > 3000:
        raise HTTPException(status_code=400, detail="summary too long (max 3000 chars)")

    # Confirm the task belongs to this incident and user
    task_res = (
        supabase.table("tasks")
        .select("id, incident_id, assigned_to, intern_role, title, feedback")
        .eq("id", task_id)
        .eq("incident_id", incident_id)
        .execute()
    )
    if not task_res.data:
        raise HTTPException(status_code=404, detail="Hotfix task not found")

    task = task_res.data[0]

    # Merge into existing feedback JSON if present, otherwise create fresh
    existing = {}
    if task.get("feedback"):
        try:
            existing = _json.loads(task["feedback"]) if isinstance(task["feedback"], str) else task["feedback"]
        except Exception:
            existing = {}

    existing["hotfix_summary"]  = summary
    existing["summary_author"]  = user.get("name") or user.get("email") or user["id"]
    existing["summary_role"]    = task.get("intern_role", "")
    existing["submitted_at"]    = datetime.now(timezone.utc).isoformat()

    supabase.table("tasks").update({
        "feedback": _json.dumps(existing),
    }).eq("id", task_id).execute()

    return {"ok": True, "task_id": task_id}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/incidents/{incident_id}/postmortem
# Collects all hotfix summaries from team members and returns them.
# When all roles have submitted, also stores a compiled postmortem_json on
# the incident row so it persists after the incident closes.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{incident_id}/postmortem")
async def get_postmortem(incident_id: str, supabase=Depends(get_supabase)):
    import json as _json

    # Fetch the incident (to check postmortem_json for resolved incidents)
    inc_res = supabase.table("incidents").select("*").eq("id", incident_id).execute()
    if not inc_res.data:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident = inc_res.data[0]

    # If a compiled postmortem was already saved, return it directly
    if incident.get("postmortem_json"):
        try:
            cached = _json.loads(incident["postmortem_json"]) if isinstance(incident["postmortem_json"], str) else incident["postmortem_json"]
            if cached.get("compiled"):
                return cached
        except Exception:
            pass

    # Fetch all hotfix tasks for this incident
    tasks_res = (
        supabase.table("tasks")
        .select("id, title, intern_role, status, feedback, assigned_to")
        .eq("incident_id", incident_id)
        .like("title", "[HOTFIX]%")
        .execute()
    )
    hotfix_tasks = tasks_res.data or []

    summaries = []
    for t in hotfix_tasks:
        fb = t.get("feedback")
        parsed = {}
        if fb:
            try:
                parsed = _json.loads(fb) if isinstance(fb, str) else fb
            except Exception:
                pass
        summaries.append({
            "task_id":     t["id"],
            "role":        t.get("intern_role", "unknown"),
            "task_title":  t.get("title", "").replace("[HOTFIX] ", ""),
            "author":      parsed.get("summary_author", "Unknown"),
            "summary":     parsed.get("hotfix_summary", None),
            "submitted_at": parsed.get("submitted_at", None),
            "task_done":   t.get("status") == "done",
        })

    all_submitted    = all(s["summary"] for s in summaries) and len(summaries) > 0
    submitted_count  = sum(1 for s in summaries if s["summary"])

    result = {
        "compiled":        False,
        "summaries":       summaries,
        "submitted_count": submitted_count,
        "total_required":  len(HOTFIX_ROLES),
        "all_submitted":   all_submitted,
        "incident_title":  incident.get("title", ""),
        "incident_status": incident.get("status", ""),
        "elapsed_seconds": incident.get("elapsed_seconds"),
    }

    # If all submitted and incident is resolved, compile and cache postmortem
    if all_submitted and incident.get("status") == "resolved":
        result["compiled"] = True
        supabase.table("incidents").update({
            "postmortem_json": _json.dumps(result),
        }).eq("id", incident_id).execute()

    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/incidents/{incident_id}/hotfix-status
# Lightweight poll for task-board locking logic.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{incident_id}/hotfix-status")
async def hotfix_status(incident_id: str, supabase=Depends(get_supabase)):
    tasks = (
        supabase.table("tasks")
        .select("id,intern_role,status,github_pr_url,title,assigned_to")
        .eq("incident_id", incident_id)
        .like("title", "[HOTFIX]%")
        .execute()
    )
    hotfix_tasks = tasks.data or []
    completed_roles = {
        t["intern_role"] for t in hotfix_tasks
        if t["status"] == "done" or t.get("github_pr_url")
    }
    return {
        "all_done":        HOTFIX_ROLES.issubset(completed_roles),
        "completed_roles": list(completed_roles),
        "required_roles":  list(HOTFIX_ROLES),
        "tasks":           hotfix_tasks,
    }