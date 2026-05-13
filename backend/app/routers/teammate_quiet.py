"""
backend/app/routers/teammate_quiet.py
──────────────────────────────────────────────────────────────────────
Teammate Goes Quiet (TGQ) Simulation Mode for InternX.

FIXES applied:
  1. Sim state persisted to `sim_state` DB table instead of an in-memory
     Python dict — survives hot reloads and server restarts.
  2. Removed per-task reassignment tracking entirely. On deactivate we
     simply look up all tasks for the inactive role in the group and
     return them to inactive_user_id — no loop-write race condition possible.

The `sim_state` table must exist in your Supabase project.
Run create_sim_state_table.sql once in the Supabase SQL Editor.

Make sure SUPABASE_SERVICE_KEY in backend/.env is the SERVICE ROLE key
(Supabase → Settings → API → service_role), NOT the anon key.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging, random, uuid
from datetime import datetime, timezone

from app.core.auth import get_current_user
from app.core.database import db, supabase_admin
from app.routers.notifications import upsert_notification

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sim/tgq", tags=["sim_tgq"])


# ─── DB-backed sim state helpers ──────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_sim(user_id: str) -> dict | None:
    """Load sim state for a user from the database. Returns None if not active."""
    try:
        import json
        res = (
            supabase_admin.table("sim_state")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            row = res.data[0]
            ts = row.get("task_snapshot", [])
            if isinstance(ts, str):
                try:
                    ts = json.loads(ts)
                except Exception:
                    ts = []
            row["task_snapshot"] = ts or []
            return row
        return None
    except Exception as e:
        logger.error(f"[TGQ] _load_sim error for user={user_id}: {e}")
        return None


def _save_sim(user_id: str, sim: dict) -> None:
    """Upsert sim state for a user into the database."""
    try:
        import json
        payload = {
            "user_id":          user_id,
            "inactive_user_id": sim.get("inactive_user_id"),
            "inactive_name":    sim.get("inactive_name"),
            "inactive_role":    sim.get("inactive_role"),
            "inactive_avatar":  sim.get("inactive_avatar"),
            "group_id":         sim.get("group_id"),
            "project_id":       sim.get("project_id"),
            "activated_at":     sim.get("activated_at"),
            "ticket_id":        sim.get("ticket_id"),
            # snapshot of task IDs that belonged to the inactive user at activation
            "task_snapshot":    json.dumps(sim.get("task_snapshot", [])),
            "updated_at":       _now(),
        }
        supabase_admin.table("sim_state").upsert(payload).execute()
    except Exception as e:
        logger.error(f"[TGQ] _save_sim error for user={user_id}: {e}")
        raise


def _delete_sim(user_id: str) -> dict | None:
    """Load then delete the sim state row. Returns the row data for use in deactivate."""
    sim = _load_sim(user_id)
    if not sim:
        return None
    try:
        supabase_admin.table("sim_state").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logger.error(f"[TGQ] _delete_sim error for user={user_id}: {e}")
    return sim


# ─── Query helpers ────────────────────────────────────────────────────────────

def _get_group_context(user_id: str) -> dict:
    """Return {group_id, project_id, intern_role} for the user."""
    gm = (
        db.table("group_members")
        .select("group_id, intern_role")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not gm.data:
        return {"group_id": None, "project_id": None, "intern_role": None}

    group_id    = gm.data[0].get("group_id")
    intern_role = gm.data[0].get("intern_role")
    project_id  = None

    if group_id:
        pg = (
            db.table("project_groups")
            .select("project_id")
            .eq("id", group_id)
            .limit(1)
            .execute()
        )
        if pg.data:
            project_id = pg.data[0].get("project_id")

    return {"group_id": group_id, "project_id": project_id, "intern_role": intern_role}


def _get_group_members(group_id: str, exclude_user_id: str) -> list[dict]:
    """Return all group members except the given user, with profile info."""
    members = (
        db.table("group_members")
        .select("user_id, intern_role")
        .eq("group_id", group_id)
        .neq("user_id", exclude_user_id)
        .execute()
    )
    if not members.data:
        return []

    user_ids = [m["user_id"] for m in members.data]
    profiles = (
        db.table("profiles")
        .select("id, name, avatar_url, intern_role")
        .in_("id", user_ids)
        .execute()
    )
    profile_map = {p["id"]: p for p in (profiles.data or [])}

    result = []
    for m in members.data:
        uid = m["user_id"]
        p   = profile_map.get(uid, {})
        result.append({
            "user_id":     uid,
            "name":        p.get("name", "Unknown"),
            "avatar_url":  p.get("avatar_url"),
            "intern_role": m.get("intern_role") or p.get("intern_role", "intern"),
        })
    return result


def _get_tasks_for_user(user_id: str, project_id: str, group_id: str | None = None) -> list[dict]:
    """Return non-done tasks assigned to a specific user."""
    cols = "id, title, status, priority, due_date, intern_role, sprint_id, description, assigned_to"

    if group_id:
        res = (
            db.table("tasks")
            .select(cols)
            .eq("assigned_to", user_id)
            .eq("group_id", group_id)
            .neq("status", "done")
            .execute()
        )
        if res.data:
            return res.data

    res = (
        db.table("tasks")
        .select(cols)
        .eq("assigned_to", user_id)
        .eq("project_id", project_id)
        .neq("status", "done")
        .execute()
    )
    return res.data or []


def _get_tasks_for_role(intern_role: str, group_id: str, project_id: str) -> list[dict]:
    """Return all non-done tasks for a given intern_role in the group."""
    cols = "id, title, status, priority, due_date, intern_role, sprint_id, description, assigned_to"

    if group_id:
        res = (
            db.table("tasks")
            .select(cols)
            .eq("intern_role", intern_role)
            .eq("group_id", group_id)
            .neq("status", "done")
            .execute()
        )
        if res.data:
            return res.data

    res = (
        db.table("tasks")
        .select(cols)
        .eq("intern_role", intern_role)
        .eq("project_id", project_id)
        .neq("status", "done")
        .execute()
    )
    return res.data or []


def _get_dependent_tasks(inactive_role: str, project_id: str, inactive_user_id: str, group_id: str | None = None) -> list[dict]:
    """Find tasks likely blocked because the inactive teammate's work is not done."""
    q = (
        db.table("tasks")
        .select("sprint_id, intern_role")
        .eq("assigned_to", inactive_user_id)
        .neq("status", "done")
    )
    if group_id:
        q = q.eq("group_id", group_id)
    else:
        q = q.eq("project_id", project_id)
    inactive_tasks = q.execute()

    if not inactive_tasks.data:
        return []

    sprint_ids = list({t["sprint_id"] for t in inactive_tasks.data if t.get("sprint_id")})
    if not sprint_ids:
        return []

    blocked = (
        db.table("tasks")
        .select("id, title, status, priority, intern_role, assigned_to")
        .in_("sprint_id", sprint_ids)
        .eq("project_id", project_id)
        .neq("assigned_to", inactive_user_id)
        .neq("intern_role", inactive_role)
        .in_("status", ["todo", "in_progress"])
        .limit(8)
        .execute()
    )
    return blocked.data or []


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/activate")
async def activate_tgq(current_user: dict = Depends(get_current_user)):
    """
    Activate TGQ simulation. Picks a random teammate as the gone-quiet member.
    Stores inactive_user_id in DB — that is all deactivate needs to restore tasks.
    """
    user_id    = current_user["id"]
    ctx        = _get_group_context(user_id)
    group_id   = ctx["group_id"]
    project_id = ctx["project_id"]

    if not group_id or not project_id:
        raise HTTPException(status_code=400, detail="You must be in a group to run this simulation.")

    members = _get_group_members(group_id, exclude_user_id=user_id)
    if not members:
        raise HTTPException(status_code=400, detail="No other teammates found in your group.")

    current_role         = ctx.get("intern_role", "")
    same_role_candidates = [
        m for m in members
        if m.get("intern_role", "").lower() == (current_role or "").lower()
    ]
    candidate_pool   = same_role_candidates if same_role_candidates else members
    inactive         = random.choice(candidate_pool)
    inactive_user_id = inactive["user_id"]

    logger.info(
        f"[TGQ] Activated: user={user_id}, inactive={inactive_user_id} "
        f"({inactive['name']} / {inactive['intern_role']})"
    )

    tasks = _get_tasks_for_user(inactive_user_id, project_id, group_id)
    if not tasks:
        role_tasks = _get_tasks_for_role(inactive["intern_role"], group_id, project_id)
        tasks = [t for t in role_tasks if t.get("assigned_to") in (None, inactive_user_id)]
    blocked = _get_dependent_tasks(inactive["intern_role"], project_id, inactive_user_id, group_id)

    # Snapshot her task IDs now — deactivate will only restore these specific tasks
    task_snapshot = [t["id"] for t in tasks]

    sim = {
        "inactive_user_id": inactive_user_id,
        "inactive_name":    inactive["name"],
        "inactive_role":    inactive["intern_role"],
        "inactive_avatar":  inactive.get("avatar_url"),
        "group_id":         group_id,
        "project_id":       project_id,
        "activated_at":     _now(),
        "ticket_id":        None,
        "task_snapshot":    task_snapshot,
    }
    _save_sim(user_id, sim)

    high_priority_count = sum(1 for t in tasks if t.get("priority") == "high")
    sprint_risk = "critical" if high_priority_count >= 2 else "high" if tasks else "medium"

    return {
        "ok":                True,
        "inactive_teammate": inactive,
        "their_tasks":       tasks,
        "blocked_tasks":     blocked,
        "sprint_risk":       sprint_risk,
        "missed_standups":   random.randint(1, 3),
        "last_seen_hours_ago": random.randint(18, 72),
        "activated_at":      sim["activated_at"],
    }


@router.get("/state")
async def get_tgq_state(current_user: dict = Depends(get_current_user)):
    """Get current TGQ simulation state. Reads from DB so accurate after restarts."""
    user_id = current_user["id"]
    sim     = _load_sim(user_id)

    if not sim:
        return {"active": False}

    inactive_user_id = sim["inactive_user_id"]
    project_id       = sim["project_id"]
    group_id         = sim["group_id"]

    tasks = _get_tasks_for_user(inactive_user_id, project_id, group_id)
    if not tasks:
        role_tasks = _get_tasks_for_role(sim["inactive_role"], group_id, project_id)
        tasks = [t for t in role_tasks if t.get("assigned_to") in (None, inactive_user_id)]
    blocked = _get_dependent_tasks(sim["inactive_role"], project_id, inactive_user_id, group_id)

    high_priority_count = sum(1 for t in tasks if t.get("priority") == "high")
    sprint_risk = "critical" if high_priority_count >= 2 else "high" if tasks else "medium"

    return {
        "active": True,
        "inactive_teammate": {
            "user_id":     inactive_user_id,
            "name":        sim["inactive_name"],
            "intern_role": sim["inactive_role"],
            "avatar_url":  sim.get("inactive_avatar"),
        },
        "their_tasks":         tasks,
        "blocked_tasks":       blocked,
        "sprint_risk":         sprint_risk,
        "ticket_id":           sim.get("ticket_id"),
        "activated_at":        sim["activated_at"],
        "missed_standups":     random.randint(1, 3),
        "last_seen_hours_ago": random.randint(18, 72),
    }


class EscalateBody(BaseModel):
    message: Optional[str] = None


@router.post("/escalate")
async def escalate_tgq(
    body: EscalateBody,
    current_user: dict = Depends(get_current_user),
):
    """Create a real escalation ticket and notify active teammates."""
    user_id = current_user["id"]
    sim     = _load_sim(user_id)

    if not sim:
        raise HTTPException(status_code=400, detail="No active TGQ simulation found.")

    group_id      = sim["group_id"]
    project_id    = sim["project_id"]
    inactive_name = sim["inactive_name"]
    inactive_role = sim["inactive_role"]

    custom_msg  = body.message or ""
    description = (
        f"[SIM: Teammate Goes Quiet] {inactive_name} ({inactive_role}) has been "
        f"inactive for the past 48+ hours. No standup submissions and zero task "
        f"movement detected. Sprint is now at HIGH RISK due to dependency delays.\n\n"
        f"{custom_msg}\n\n"
        f"AI Scrum Master recommendation: Reassign critical tasks or negotiate "
        f"sprint scope with stakeholders."
    ).strip()

    ticket_payload = {
        "id":            str(uuid.uuid4()),
        "title":         f"[SIM] Dependency delay: {inactive_role.title()} not delivered",
        "description":   description,
        "type":          "dependency_delay",
        "priority":      "high",
        "status":        "open",
        "project_id":    project_id,
        "from_group_id": group_id,
        "to_group_id":   group_id,
        "created_by":    user_id,
        "created_at":    _now(),
        "updated_at":    _now(),
    }

    try:
        ticket_res = supabase_admin.table("tickets").insert(ticket_payload).execute()
        ticket_id  = ticket_res.data[0]["id"] if ticket_res.data else ticket_payload["id"]
        sim["ticket_id"] = ticket_id
        _save_sim(user_id, sim)
    except Exception as e:
        logger.error(f"[TGQ] Failed to create ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to create escalation ticket.")

    try:
        members = _get_group_members(group_id, exclude_user_id=sim["inactive_user_id"])
        for member in members:
            mid = member["user_id"]
            if mid == user_id:
                continue
            upsert_notification(
                user_id=mid,
                key="tgq_escalation",
                type_="warning",
                title="⚠️ Sprint Risk: Teammate Inactive",
                body=f"{inactive_name} ({inactive_role}) has gone quiet. Ticket raised. Check your tasks for blockers.",
                icon="🔕",
                href=f"/dashboard/ticket/{ticket_id}",
            )
        upsert_notification(
            user_id=user_id,
            key="tgq_escalation_self",
            type_="info",
            title="📋 Escalation Ticket Created",
            body=f"Ticket raised for {inactive_name}'s inactivity. AI Scrum Master has flagged sprint as HIGH RISK.",
            icon="🎫",
            href=f"/dashboard/ticket/{ticket_id}",
        )
    except Exception as e:
        logger.warning(f"[TGQ] Notification error (non-fatal): {e}")

    logger.info(f"[TGQ] Escalated for user={user_id}, ticket={ticket_id}")

    return {
        "ok":           True,
        "ticket_id":    ticket_id,
        "ticket_title": ticket_payload["title"],
        "message":      "Escalation ticket created and teammates notified.",
    }


class ReassignBody(BaseModel):
    task_id: str


@router.post("/reassign")
async def reassign_task(
    body: ReassignBody,
    current_user: dict = Depends(get_current_user),
):
    """
    Reassign one of the inactive teammate's tasks to the current user.
    No tracking needed — deactivate restores by role query directly.
    """
    user_id = current_user["id"]
    sim     = _load_sim(user_id)

    if not sim:
        raise HTTPException(status_code=400, detail="No active TGQ simulation found.")

    task_id = body.task_id

    task_res = (
        db.table("tasks")
        .select("id, title, assigned_to, status")
        .eq("id", task_id)
        .eq("assigned_to", sim["inactive_user_id"])
        .limit(1)
        .execute()
    )
    if not task_res.data:
        raise HTTPException(status_code=404, detail="Task not found or not assigned to inactive teammate.")

    task = task_res.data[0]

    update_res = (
        supabase_admin.table("tasks")
        .update({
            "assigned_to": user_id,
            "status":      "in_progress",
            "updated_at":  _now(),
        })
        .eq("id", task_id)
        .execute()
    )
    if not update_res.data:
        raise HTTPException(status_code=500, detail="Failed to reassign task.")

    upsert_notification(
        user_id=user_id,
        key=f"tgq_reassign_{task_id}",
        type_="task",
        title="📥 Task Reassigned to You",
        body=f"[SIM] '{task['title']}' was picked up from {sim['inactive_name']}. You're now responsible.",
        icon="📋",
        href="/internship/tasks",
    )

    logger.info(f"[TGQ] Task {task_id} reassigned from {sim['inactive_user_id']} to {user_id}")

    return {
        "ok":         True,
        "task_id":    task_id,
        "task_title": task["title"],
        "message":    f"Task reassigned to you from {sim['inactive_name']}.",
    }


@router.post("/auto-reassign")
async def auto_reassign_tasks(current_user: dict = Depends(get_current_user)):
    """
    Automatically reassign ALL open tasks from the inactive teammate to
    active same-role teammates (round-robin).

    No per-task tracking — deactivate restores everything by querying
    the inactive role and setting assigned_to = inactive_user_id directly.
    """
    user_id = current_user["id"]
    sim     = _load_sim(user_id)

    if not sim:
        raise HTTPException(status_code=400, detail="No active TGQ simulation found.")

    inactive_user_id = sim["inactive_user_id"]
    inactive_name    = sim["inactive_name"]
    inactive_role    = sim["inactive_role"]
    group_id         = sim["group_id"]
    project_id       = sim["project_id"]

    # Get tasks belonging to the inactive user
    tasks = _get_tasks_for_user(inactive_user_id, project_id, group_id)
    if not tasks:
        role_tasks = _get_tasks_for_role(inactive_role, group_id, project_id)
        tasks = [t for t in role_tasks if t.get("assigned_to") in (None, inactive_user_id)]

    if not tasks:
        return {
            "ok":         True,
            "reassigned": [],
            "failed":     [],
            "total":      0,
            "message":    f"No open tasks found for role '{inactive_role}' in this group.",
        }

    # Build active pool — same-role first, fall back to everyone
    all_members = _get_group_members(group_id, exclude_user_id=inactive_user_id)
    same_role_members = [
        m for m in all_members
        if m.get("intern_role", "").lower() == (inactive_role or "").lower()
    ]
    active_pool = same_role_members if same_role_members else all_members

    # Ensure the requesting user is always in the pool
    if not any(m["user_id"] == user_id for m in active_pool):
        prof = db.table("profiles").select("id, name, avatar_url, intern_role").eq("id", user_id).limit(1).execute()
        if prof.data:
            p = prof.data[0]
            active_pool.append({
                "user_id":     user_id,
                "name":        p.get("name", "You"),
                "avatar_url":  p.get("avatar_url"),
                "intern_role": p.get("intern_role", inactive_role),
            })

    if not active_pool:
        raise HTTPException(status_code=400, detail="No active teammates available to reassign tasks to.")

    reassigned = []
    failed     = []

    for idx, task in enumerate(tasks):
        assignee    = active_pool[idx % len(active_pool)]
        assignee_id = assignee["user_id"]

        try:
            supabase_admin.table("tasks").update({
                "assigned_to": assignee_id,
                "status":      "in_progress",
                "updated_at":  _now(),
            }).eq("id", task["id"]).execute()

            upsert_notification(
                user_id=assignee_id,
                key=f"tgq_auto_reassign_{task['id']}",
                type_="task",
                title="📥 Task Assigned to You (Sprint Recovery)",
                body=(
                    f"[SIM] '{task['title']}' was auto-reassigned from "
                    f"{inactive_name} ({inactive_role}). "
                    f"You've been picked to keep the sprint on track!"
                ),
                icon="📋",
                href="/internship/tasks",
            )

            reassigned.append({
                "task_id":       task["id"],
                "task_title":    task["title"],
                "assigned_to":   assignee_id,
                "assignee_name": assignee.get("name", "Teammate"),
                "assignee_role": assignee.get("intern_role", inactive_role),
            })

        except Exception as e:
            logger.error(f"[TGQ] Auto-reassign failed for task {task['id']}: {e}")
            failed.append({"task_id": task["id"], "task_title": task["title"], "error": str(e)})

    try:
        upsert_notification(
            user_id=user_id,
            key="tgq_auto_reassign_summary",
            type_="info",
            title="✅ Tasks Auto-Reassigned",
            body=(
                f"[SIM] {len(reassigned)} of {len(tasks)} tasks from {inactive_name} "
                f"have been redistributed to active teammates."
            ),
            icon="🔄",
            href="/internship/tasks",
        )
    except Exception as e:
        logger.warning(f"[TGQ] Summary notification error (non-fatal): {e}")

    logger.info(f"[TGQ] Auto-reassign: user={user_id}, reassigned={len(reassigned)}, failed={len(failed)}")

    return {
        "ok":         True,
        "reassigned": reassigned,
        "failed":     failed,
        "total":      len(tasks),
        "message":    f"{len(reassigned)} task(s) redistributed across {len(active_pool)} active teammate(s).",
    }


@router.post("/deactivate")
async def deactivate_tgq(current_user: dict = Depends(get_current_user)):
    """
    End TGQ simulation and restore all tasks to the inactive teammate.

    No tracking array needed. We already stored inactive_user_id and
    inactive_role at activation. On end, we find all tasks for that role
    in the group and return them all to inactive_user_id in one shot.
    """
    user_id = current_user["id"]
    sim     = _delete_sim(user_id)  # reads row, deletes it, returns the data

    if not sim:
        return {"ok": True, "message": "No active simulation to deactivate."}

    inactive_user_id = sim["inactive_user_id"]
    inactive_name    = sim["inactive_name"]
    inactive_role    = sim["inactive_role"]
    group_id         = sim["group_id"]
    project_id       = sim["project_id"]

    # Only restore the exact tasks that belonged to her at sim start.
    # This guarantees your own tasks are never touched.
    task_snapshot = sim.get("task_snapshot", [])

    if not task_snapshot:
        logger.warning(f"[TGQ] No task_snapshot found for user={user_id}, nothing to restore.")
        return {
            "ok":             True,
            "restored_count": 0,
            "failed_count":   0,
            "message":        f"TGQ simulation ended. No tasks to restore for {inactive_name}.",
        }

    restored = []
    failed   = []

    for task_id in task_snapshot:
        try:
            supabase_admin.table("tasks").update({
                "assigned_to": inactive_user_id,
                "status":      "todo",
                "updated_at":  _now(),
            }).eq("id", task_id).execute()

            restored.append(task_id)

            upsert_notification(
                user_id=inactive_user_id,
                key=f"tgq_restore_{task_id}",
                type_="task",
                title="📋 Task Returned to You",
                body="[SIM ended] A task that was temporarily reassigned during the simulation has been returned to you.",
                icon="🔄",
                href="/internship/tasks",
            )
        except Exception as e:
            logger.error(f"[TGQ] Failed to restore task {task_id}: {e}")
            failed.append(task_id)

    logger.info(
        f"[TGQ] Deactivated: user={user_id}, "
        f"restored={len(restored)}/{len(role_tasks)}, failed={len(failed)}"
    )

    return {
        "ok":             True,
        "restored_count": len(restored),
        "failed_count":   len(failed),
        "message": (
            f"TGQ simulation ended. {len(restored)} task(s) returned to {inactive_name}."
            if restored else
            "TGQ simulation ended. No tasks needed restoration."
        ),
    }


@router.get("/debug")
async def debug_tgq(current_user: dict = Depends(get_current_user)):
    """Debug: show DB state for the current user's group and active sim."""
    user_id    = current_user["id"]
    ctx        = _get_group_context(user_id)
    group_id   = ctx["group_id"]
    project_id = ctx["project_id"]

    members_raw = (
        db.table("group_members")
        .select("user_id, intern_role")
        .eq("group_id", group_id)
        .execute()
    )
    tasks_by_group = (
        db.table("tasks")
        .select("id, title, assigned_to, intern_role, status, group_id, project_id, sprint_id")
        .eq("group_id", group_id)
        .neq("status", "done")
        .execute()
    )
    tasks_by_project = (
        db.table("tasks")
        .select("id, title, assigned_to, intern_role, status, group_id, project_id, sprint_id")
        .eq("project_id", project_id)
        .neq("status", "done")
        .limit(20)
        .execute()
    )

    sim = _load_sim(user_id)

    return {
        "user_id":                    user_id,
        "group_id":                   group_id,
        "project_id":                 project_id,
        "current_role":               ctx.get("intern_role"),
        "group_members":              members_raw.data or [],
        "tasks_by_group_id":          tasks_by_group.data or [],
        "tasks_by_project_id_sample": tasks_by_project.data or [],
        "sim_state": {
            "active":           bool(sim),
            "inactive_user_id": sim.get("inactive_user_id") if sim else None,
            "inactive_role":    sim.get("inactive_role") if sim else None,
        } if sim else {"active": False},
    }