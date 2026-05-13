"""
backend/app/routers/tasks.py
─────────────────────────────
Task endpoints for the intern-facing dashboard.

FIXES applied:

  1. _get_active_project_id — two flat queries only, no nested PostgREST joins.

  2. _resolve_active_sprint_for_user — no .ilike(), Python-side role filtering.

  3. get_my_tasks fallback — role-scoped (intern only sees their own role tasks).

  4. update_task_status — sprint_id resolution made more robust; adaptive engine
     always runs in background when a task is marked done.

  5. _resolve_active_sprint_for_user Step 2 — also checks tasks with status
     'todo' and 'in_progress' (not just neq done) so brand-new interns whose
     tasks are all still 'todo' are found correctly.

  6. get_my_tasks — returns sprint metadata alongside tasks so the frontend
     always knows which sprint is active.

  7. get_my_tasks — CCR tasks (mid_sprint_changed=true) are always included
     regardless of their sprint_id so they always appear on the Kanban board
     even if the CCR trigger wrote a sentinel/wrong sprint_id.

  8. _resolve_active_sprint_for_user Step 2 — excludes CCR sentinel tasks
     (mid_sprint_changed=true) from sprint resolution so a previously created
     CCR task with a sentinel sprint_id doesn't poison the active-sprint lookup.

  9. update_task_status — sentinel sprint_id detection (SENTINEL_PREFIX) added
     on top of the missing-sprint-id fallback so CCR tasks always resolve to the
     correct real sprint before firing mid-sprint change and adaptive engine.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.core.auth import get_current_user
from app.core.database import db, supabase_admin
import logging
import time
from datetime import date

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_active_project_id(user_id: str) -> str | None:
    """
    Returns the user's currently active project_id.

    Lookup order:
      1. group_members → project_groups.project_id  (authoritative)
      2. profiles.project_id                         (fast cache / fallback)

    Flat queries only — no nested PostgREST joins.
    """
    gm = (
        db.table("group_members")
        .select("group_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if gm.data and gm.data[0].get("group_id"):
        group_id = gm.data[0]["group_id"]
        pg = (
            db.table("project_groups")
            .select("project_id")
            .eq("id", group_id)
            .limit(1)
            .execute()
        )
        if pg.data and pg.data[0].get("project_id"):
            return pg.data[0]["project_id"]

    profile = (
        db.table("profiles")
        .select("project_id")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if profile.data and profile.data[0].get("project_id"):
        return profile.data[0]["project_id"]

    return None


def _get_user_context(user_id: str) -> dict:
    """
    Returns {project_id, group_id, intern_role} for the user.
    Flat queries only.
    """
    gm = (
        db.table("group_members")
        .select("group_id, intern_role")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if gm.data:
        row         = gm.data[0]
        group_id    = row.get("group_id")
        intern_role = row.get("intern_role")
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
        if project_id:
            return {
                "project_id":  project_id,
                "group_id":    group_id,
                "intern_role": intern_role,
            }

    profile = (
        db.table("profiles")
        .select("project_id, intern_role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if profile.data:
        return {
            "project_id":  profile.data[0].get("project_id"),
            "group_id":    None,
            "intern_role": profile.data[0].get("intern_role"),
        }
    return {"project_id": None, "group_id": None, "intern_role": None}


def _resolve_active_sprint_for_user(user_id: str) -> dict | None:
    """
    Find the active sprint for this user based on their role and group.

    Strategy (no .ilike() — Python-side string filtering everywhere):
      1. Resolve group membership → group_id, intern_role, project_id.
      2. Find sprint(s) that the user has assigned tasks in AND that are active.
         - Excludes CCR tasks (mid_sprint_changed=true) whose sentinel sprint_id
           would poison the lookup (FIX 8 / v1).
         - Fetches all task statuses including 'todo' so brand-new interns are
           handled correctly (FIX 5 / v2).
      3. Fallback: fetch ALL active sprints for the project, then filter by
         role title + group_id in Python.
      4. Final fallback: first active sprint in the project.
    """
    # ── Step 1: Resolve user's group context ─────────────────────────────────
    gm = (
        db.table("group_members")
        .select("group_id, intern_role")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    group_id    = None
    intern_role = None
    project_id  = None

    if gm.data:
        group_id    = gm.data[0].get("group_id")
        intern_role = gm.data[0].get("intern_role")

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

    if not project_id:
        project_id = _get_active_project_id(user_id)

    if not project_id:
        return None

    # ── Step 2: Best signal — sprint from user's NON-CCR assigned tasks ───────
    # FIX 8 (v1): exclude mid_sprint_changed=true so CCR sentinel sprint_ids
    #   don't poison this lookup.
    # FIX 5 (v2): fetch all statuses (no status filter) so brand-new interns
    #   whose tasks are all still 'todo' are found correctly.
    task_res = (
        db.table("tasks")
        .select("sprint_id")
        .eq("assigned_to", user_id)
        .eq("project_id", project_id)
        .eq("mid_sprint_changed", False)          # ← exclude CCR sentinel tasks
        .not_.is_("sprint_id", "null")
        .execute()
    )
    sprint_ids = list({
        row["sprint_id"]
        for row in (task_res.data or [])
        if row.get("sprint_id")
    })

    if sprint_ids:
        sprints_res = (
            db.table("sprints")
            .select("*")
            .in_("id", sprint_ids)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if sprints_res.data:
            return sprints_res.data[0]

    # ── Step 3: Fetch all active sprints, filter in Python (no .ilike()) ─────
    all_active_res = (
        db.table("sprints")
        .select("*")
        .eq("project_id", project_id)
        .eq("is_active", True)
        .execute()
    )
    all_active = all_active_res.data or []

    if intern_role and all_active:
        role_needle = intern_role.replace("_", " ").lower()

        # Priority 1: group-scoped + role title match
        if group_id:
            for s in all_active:
                title = (s.get("title") or "").lower()
                if role_needle in title and s.get("group_id") == group_id:
                    return s

        # Priority 2: project-wide role title match
        for s in all_active:
            title = (s.get("title") or "").lower()
            if role_needle in title:
                return s

    # ── Step 4: Any active sprint in the project ──────────────────────────────
    return all_active[0] if all_active else None


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/my-tasks")
async def get_my_tasks(current_user: dict = Depends(get_current_user)):
    """
    Returns { sprint, tasks } where sprint is the active sprint (or null) and
    tasks are those assigned to this user in that sprint.

    FIX 7 (v1): CCR tasks (mid_sprint_changed=true) are fetched separately and
    merged into the result regardless of their sprint_id. This ensures they
    always appear on the Kanban board even when the CCR trigger wrote a
    sentinel/wrong sprint_id that doesn't exist in the sprints table.

    AUTO-BOOTSTRAP: If an active sprint exists but the user has no assigned
    tasks yet (they joined a pre-seeded project without the normal join flow),
    this calls initialise_sprint_for_intern to build the pool and assign 2
    initial tasks, then re-fetches. This is idempotent and safe to call on
    every page load.

    FIX 3: Fallback is role-scoped — intern only sees their own role tasks.
    FIX 6: Always returns sprint metadata alongside tasks.
    """
    user_id     = current_user["id"]
    ctx         = _get_user_context(user_id)
    project_id  = ctx["project_id"]
    group_id    = ctx["group_id"]
    intern_role = ctx["intern_role"]

    sprint = _resolve_active_sprint_for_user(user_id)

    # ── Fetch regular (non-CCR) tasks for the resolved sprint ────────────────
    query = db.table("tasks").select("*").eq("assigned_to", user_id)
    if sprint:
        query = query.eq("sprint_id", sprint["id"])
    elif project_id:
        query = query.eq("project_id", project_id)
        if intern_role:
            query = query.eq("intern_role", intern_role)

    result = query.execute()
    tasks  = result.data or []

    # ── FIX 7 (v1): Always fetch CCR tasks separately and merge ──────────────
    # CCR tasks may have a sentinel or mismatched sprint_id so they won't be
    # returned by the sprint-scoped query above. We fetch them by project +
    # assigned_to + mid_sprint_changed=true and merge, deduplicating by id.
    if project_id:
        try:
            ccr_res = (
                db.table("tasks")
                .select("*")
                .eq("assigned_to", user_id)
                .eq("project_id", project_id)
                .eq("mid_sprint_changed", True)
                .execute()
            )
            ccr_tasks = ccr_res.data or []

            if ccr_tasks:
                existing_ids = {t["id"] for t in tasks}
                new_ccr = [t for t in ccr_tasks if t["id"] not in existing_ids]
                tasks = tasks + new_ccr
                logger.info(
                    f"[Tasks] Merged {len(new_ccr)} CCR task(s) for user={user_id}"
                )
        except Exception as e:
            logger.warning(f"[Tasks] CCR task merge failed for user={user_id}: {e}")

    # AUTO-BOOTSTRAP: sprint found but zero tasks assigned to this user.
    if sprint and not tasks and project_id and intern_role:
        try:
            from app.services.adaptive_engine import initialise_sprint_for_intern
            logger.info(
                f"[Tasks] Auto-bootstrapping sprint={sprint['id']} "
                f"for user={user_id} role={intern_role}"
            )
            initialise_sprint_for_intern(
                user_id=user_id,
                project_id=project_id,
                group_id=group_id,
                intern_role=intern_role,
            )
            refetch = (
                db.table("tasks")
                .select("*")
                .eq("assigned_to", user_id)
                .eq("sprint_id", sprint["id"])
                .execute()
            )
            tasks = refetch.data or []
        except Exception as e:
            logger.error(
                f"[Tasks] Auto-bootstrap failed user={user_id}: {e}",
                exc_info=True,
            )

    return {
        "sprint": sprint,
        "tasks":  tasks,
    }


@router.get("/project-tasks")
async def get_project_tasks(current_user: dict = Depends(get_current_user)):
    """All assigned tasks for the current user's project (for Teammates page)."""
    user_id    = current_user["id"]
    project_id = _get_active_project_id(user_id)

    if not project_id:
        return []

    result = (
        db.table("tasks")
        .select(
            "id, title, description, status, priority, due_date, "
            "assigned_to, updated_at, created_at, score, feedback, "
            "github_pr_url, sprint_id, difficulty, intern_role"
        )
        .eq("project_id", project_id)
        .not_.is_("assigned_to", "null")
        .execute()
    )
    return result.data or []


@router.get("/sprints/active")
async def get_active_sprint(current_user: dict = Depends(get_current_user)):
    """
    Returns the active sprint for the current user as a list (frontend expects array).
    """
    user_id = current_user["id"]
    sprint  = _resolve_active_sprint_for_user(user_id)
    if sprint:
        return [sprint]
    return []


@router.get("/active-task")
async def get_active_task(current_user: dict = Depends(get_current_user)):
    """Returns the most recently created in-progress task for the user."""
    user_id    = current_user["id"]
    project_id = _get_active_project_id(user_id)

    query = (
        db.table("tasks")
        .select("id, title, status")
        .eq("assigned_to", user_id)
        .eq("status", "in_progress")
    )
    if project_id:
        query = query.eq("project_id", project_id)

    result = query.order("created_at", desc=True).limit(1).execute()
    if not result.data:
        return {"task_id": None, "title": None}
    return {"task_id": result.data[0]["id"], "title": result.data[0]["title"]}


@router.get("/{task_id}")
async def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    result = db.table("tasks").select("*").eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    task = dict(result.data[0])
    if task.get("project_id"):
        gm = (
            db.table("group_members")
            .select("group_id")
            .eq("user_id", current_user["id"])
            .execute()
        )
        if gm.data:
            task["group_id"] = gm.data[0]["group_id"]
    return task


@router.patch("/{task_id}/status")
async def update_task_status(
    task_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    valid_statuses = ["todo", "in_progress", "review", "done"]
    status = body.get("status")
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {valid_statuses}",
        )

    result = db.table("tasks").update({"status": status}).eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")

    updated_task = result.data[0]

    if status == "done":
        user_id   = current_user["id"]
        sprint_id = updated_task.get("sprint_id")

        # ── Resolve sprint_id if missing or sentinel (FIX 9) ─────────────────
        # v1 adds sentinel detection on top of v2's missing-sprint-id fallback.
        # CCR tasks may carry a sentinel sprint_id (ffffffff-fe00-...); both
        # cases must be resolved to the real active sprint before firing
        # mid-sprint change and the adaptive engine.
        SENTINEL_PREFIX = "ffffffff"
        is_sentinel = sprint_id and str(sprint_id).startswith(SENTINEL_PREFIX)

        if not sprint_id or is_sentinel:
            try:
                from app.routers.mid_sprint_change import _get_active_sprint_for_user
                sprint_id = _get_active_sprint_for_user(user_id)
            except Exception as e:
                logger.error(
                    f"[Tasks] Could not resolve sprint_id via mid_sprint_change: {e}"
                )

        if not sprint_id or is_sentinel:
            sprint_obj = _resolve_active_sprint_for_user(user_id)
            if sprint_obj:
                sprint_id = sprint_obj.get("id")
                if sprint_id:
                    db.table("tasks").update({"sprint_id": sprint_id}).eq("id", task_id).execute()
                    logger.info(
                        f"[Tasks] Backfilled sprint_id={sprint_id} on task={task_id}"
                    )

        # ── Mid-Sprint Change ─────────────────────────────────────────────────
        if sprint_id:
            try:
                from app.routers.mid_sprint_change import (
                    _get_user_role,
                    _delayed_change_job_sync,
                )
                import random
                role  = _get_user_role(user_id)
                delay = random.randint(300, 600)
                background_tasks.add_task(
                    _delayed_change_job_sync,
                    user_id=user_id,
                    sprint_id=sprint_id,
                    role=role,
                    delay_seconds=delay,
                )
            except Exception as e:
                logger.error(
                    f"[MidSprintChange] Failed to schedule: {e}", exc_info=True
                )

        # ── Adaptive Engine ───────────────────────────────────────────────────
        background_tasks.add_task(
            _run_adaptive_on_done,
            user_id=user_id,
            task_id=task_id,
        )

    return updated_task


def _run_adaptive_on_done(user_id: str, task_id: str) -> None:
    """Background job: calls the adaptive engine when a task is marked done."""
    time.sleep(1)
    try:
        from app.services.adaptive_engine import on_task_done
        result = on_task_done(user_id=user_id, task_id=task_id)
        action = result.get("action", "none")
        if action == "assigned":
            logger.info(
                f"[AdaptiveEngine] New task → user={user_id}: "
                f"'{result.get('task_title')}' ({result.get('difficulty')}) "
                f"score={result.get('score', 0):.1f}"
            )
        elif action == "sprint_advanced":
            logger.info(
                f"[AdaptiveEngine] Team advanced to sprint "
                f"'{result.get('new_sprint_title')}' triggered by user={user_id}"
            )
        elif action == "waiting":
            logger.info(
                f"[AdaptiveEngine] user={user_id} finished pool; "
                f"waiting for teammates"
            )
        else:
            logger.debug(
                f"[AdaptiveEngine] on_task_done result: {result.get('reason', 'no action')}"
            )
    except Exception as e:
        logger.error(f"[AdaptiveEngine] Background job error: {e}", exc_info=True)


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    allowed = {
        "title", "description", "status", "priority",
        "due_date", "resources", "github_pr_url",
    }
    update_data = {k: v for k, v in body.items() if k in allowed}
    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    result = db.table("tasks").update(update_data).eq("id", task_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")
    return result.data[0]