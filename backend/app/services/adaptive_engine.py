"""
backend/app/services/adaptive_engine.py
========================================
Adaptive Difficulty Engine — Pool-Based Sprint Task Assignment

Architecture:
─────────────
Project
└── Per-role sprint track  (team = same project_id + group_id + intern_role)
      Sprint 0 → Sprint 1 → Sprint 2 …

  4 roles × 3 sprints = 12 sprints total per project (frontend, backend, tester, ui_ux etc.)

Pool per sprint per role:
  pool_size = ceil(member_count × 3.5)
  ~43% easy | ~43% medium | ~14% hard
  Example: 2 backend members → pool=7 → 3 easy + 3 medium + 1 hard

Initial assignment:
  Every intern gets exactly 2 tasks: 1 easy + 1 medium
  Remaining tasks sit UNASSIGNED in the pool

Mid-sprint adaptive assignment:
  Intern marks task done → todo list empty?
    Yes → compute score → pick 1 task from pool matching difficulty → assign
    Repeat until pool exhausted

Score formula:
  base         = avg PR score across done tasks (0–100)
  time_bonus   = +10 if submitted before due_date, −5 if late  (per task)
  resubmit_pen = −8 per extra PR attempt beyond first           (per task)
  final        = clamp(base + time_bonus − resubmit_pen, 0, 100)
  0–40  → easy | 41–70 → medium | 71–100 → hard

Sprint advance:
  Pool exhausted for an intern AND all teammates' assigned tasks are done
  → deactivate current sprint, create next sprint, build new pool,
    assign 1 easy + 1 medium to every team member, activate new sprint

FIXES in this version:
──────────────────────
  FIX 1  — intern_role is NEVER None/empty when passed to DB queries.
            All callers validate intern_role before use; on_task_done
            returns early if intern_role is falsy.

  FIX 2  — build_task_pool Source 1 handles NULL intern_role in seed/template
            tasks with a two-pass search. Stamping happens BEFORE insert so
            the NOT NULL constraint on tasks.intern_role is never violated.

  FIX 3  — _advance_sprint_for_team race-condition guard: after deactivating
            current sprint, re-check whether next sprint already exists and
            is active before creating a duplicate.

  FIX 4  — get_or_create_role_sprint Sprint-0 adoption fallback now scopes
            by group_id AND role title (Python-side, no .ilike()).
            Sprint N>0 never adopts — always creates fresh.

  FIX 5  — assign_initial_tasks hard guard: if intern already has ≥ 2
            assigned tasks in this sprint, skip entirely.

  FIX 6  — build_task_pool "already full" guard compares against total tasks
            in sprint (assigned + unassigned) for this role.

  FIX 7  — get_or_create_role_sprint no longer unconditionally activates a
            found sprint. Activation is the caller's responsibility. Only
            Sprint-0 creation sets is_active=True.

  FIX 8  — _advance_sprint_for_team: sprint is activated AFTER pool is built
            and initial tasks are assigned to prevent race-window where interns
            see an active sprint with no tasks.

  FIX 9  — _pick_from_pool: intern_role empty-string guard — returns None
            immediately if role is falsy rather than querying with "" which
            could match nothing or cause unexpected results.

  FIX 10 — build_task_pool: template insert now builds the full difficulty
            distribution correctly by iterating need_easy + need_medium +
            need_hard tasks (not cycling indefinitely on pool_size).

  FIX 11 — on_task_done returns early if intern_role is None/empty so the
            engine never runs with a missing role context.

All queries are flat (no nested PostgREST joins) to avoid Cloudflare Error 1101.
No .ilike() calls anywhere — Python-side string filtering used instead.
"""

import logging
import math
import re
import uuid
import itertools
from datetime import datetime, date, timedelta, timezone

from app.core.database import supabase_admin as db
from app.routers.notifications import upsert_notification

logger = logging.getLogger(__name__)

DIFFICULTY_TIERS = ("easy", "medium", "hard")

VALID_INTERN_ROLES = {
    "frontend", "backend", "fullstack", "devops", "design", "tester", "ui_ux"
}


# ── Utilities ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pool_split(pool_size: int) -> tuple[int, int, int]:
    """
    Split pool_size into (easy, medium, hard): ~43% / ~43% / ~14%.

    Examples:
      pool=7  → 3 easy, 3 medium, 1 hard
      pool=4  → 2 easy, 1 medium, 1 hard
      pool=1  → 0 easy, 0 medium, 1 hard
    """
    n_hard   = max(1, round(pool_size * 0.14))
    n_easy   = round(pool_size * 0.43)
    n_medium = pool_size - n_easy - n_hard
    n_medium = max(0, n_medium)
    n_easy   = max(0, pool_size - n_medium - n_hard)
    return n_easy, n_medium, n_hard


def _get_sprint_number(sprint_title: str) -> int | None:
    m = re.match(r"Sprint\s+(\d+)", sprint_title or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def _role_title(intern_role: str) -> str:
    """'backend' → 'Backend', 'ui_ux' → 'Ui Ux'"""
    return intern_role.replace("_", " ").title()


def _sprint_title(sprint_number: int, intern_role: str) -> str:
    return f"Sprint {sprint_number} — {_role_title(intern_role)}"


# ── User context ───────────────────────────────────────────────────────────────

def _get_user_context(user_id: str) -> dict:
    """
    Returns {project_id, group_id, intern_role} for the user.
    Flat queries only — no nested PostgREST selects.
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

    # Fallback: profiles table
    prof = (
        db.table("profiles")
        .select("project_id, intern_role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if prof.data:
        return {
            "project_id":  prof.data[0].get("project_id"),
            "group_id":    None,
            "intern_role": prof.data[0].get("intern_role"),
        }
    return {"project_id": None, "group_id": None, "intern_role": None}


def _count_role_members(
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> int:
    """Count members sharing the same group + role (the team)."""
    query = (
        db.table("group_members")
        .select("id", count="exact")
        .eq("intern_role", intern_role)
    )
    if group_id:
        query = query.eq("group_id", group_id)
    else:
        grp = (
            db.table("project_groups")
            .select("id")
            .eq("project_id", project_id)
            .execute()
        )
        gids = [g["id"] for g in (grp.data or [])]
        if gids:
            query = query.in_("group_id", gids)
    result = query.execute()
    return max(1, result.count or 0)


def _get_role_member_ids(
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> list[str]:
    """Return user_ids for the entire team (project + group + role)."""
    query = (
        db.table("group_members")
        .select("user_id")
        .eq("intern_role", intern_role)
    )
    if group_id:
        query = query.eq("group_id", group_id)
    else:
        grp = (
            db.table("project_groups")
            .select("id")
            .eq("project_id", project_id)
            .execute()
        )
        gids = [g["id"] for g in (grp.data or [])]
        if gids:
            query = query.in_("group_id", gids)
    result = query.execute()
    return [r["user_id"] for r in (result.data or [])]


# ── Performance score ──────────────────────────────────────────────────────────

def compute_performance_score(user_id: str, sprint_id: str) -> dict:
    """
    Compute adaptive difficulty score from done tasks in a sprint.

    Formula:
      base         = avg PR score (0–100)
      time_bonus   = +10 on-time, −5 late  (per task)
      resubmit_pen = −8 per extra attempt   (per task)
      final        = clamp(base + time_bonus − resubmit_pen, 0, 100)

    Tier: 0–40 → easy | 41–70 → medium | 71–100 → hard
    """
    tasks_res = (
        db.table("tasks")
        .select("id, score, due_date, updated_at, status")
        .eq("assigned_to", user_id)
        .eq("sprint_id", sprint_id)
        .eq("status", "done")
        .execute()
    )
    tasks = tasks_res.data or []

    if not tasks:
        return {
            "performance_score": 0.0,
            "difficulty_tier":   "easy",
            "breakdown": {
                "avg_task_score":   0.0,
                "time_adjustment":  0,
                "resubmit_penalty": 0,
                "task_count":       0,
            },
        }

    scored    = [t for t in tasks if t.get("score") is not None]
    avg_score = sum(t["score"] for t in scored) / len(scored) if scored else 0.0

    time_adj = 0
    for t in tasks:
        due, done_at = t.get("due_date"), t.get("updated_at")
        if due and done_at:
            try:
                due_dt  = datetime.fromisoformat(due.replace("Z", "+00:00"))
                done_dt = datetime.fromisoformat(done_at.replace("Z", "+00:00"))
                time_adj += 10 if done_dt <= due_dt else -5
            except Exception:
                pass

    resubmit_pen = 0
    task_ids = [t["id"] for t in tasks]
    if task_ids:
        try:
            from collections import Counter
            att = (
                db.table("review_attempts")
                .select("task_id")
                .in_("task_id", task_ids)
                .eq("user_id", user_id)
                .execute()
            )
            for cnt in Counter(a["task_id"] for a in (att.data or [])).values():
                if cnt > 1:
                    resubmit_pen += (cnt - 1) * 8
        except Exception as e:
            logger.warning(f"[AdaptiveEngine] review_attempts fetch failed: {e}")

    final = max(0.0, min(100.0, avg_score + time_adj - resubmit_pen))
    tier  = "easy" if final <= 40 else ("medium" if final <= 70 else "hard")

    return {
        "performance_score": round(final, 2),
        "difficulty_tier":   tier,
        "breakdown": {
            "avg_task_score":   round(avg_score, 2),
            "time_adjustment":  time_adj,
            "resubmit_penalty": -resubmit_pen,
            "task_count":       len(tasks),
        },
    }


# ── Sprint completion check ────────────────────────────────────────────────────

def _is_sprint_complete_for_team(
    sprint_id: str,
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> bool:
    """
    Sprint is complete when every ASSIGNED task in the sprint for this team
    (same project + group + role) is 'done'.
    Unassigned pool tasks are intentionally ignored — they are reserve tasks.
    Returns False if there are no assigned tasks at all.
    """
    team_ids = _get_role_member_ids(project_id, group_id, intern_role)
    if not team_ids:
        return False

    res = (
        db.table("tasks")
        .select("id, status")
        .eq("sprint_id", sprint_id)
        .eq("project_id", project_id)
        .in_("assigned_to", team_ids)
        .execute()
    )
    assigned = res.data or []
    if not assigned:
        return False

    return all(t.get("status") == "done" for t in assigned)


# ── Sprint creation / lookup ───────────────────────────────────────────────────

def get_or_create_role_sprint(
    project_id: str,
    group_id: str | None,
    intern_role: str,
    sprint_number: int,
    created_by: str,
) -> dict:
    """
    Finds or creates a sprint titled "Sprint {N} — {Role}" for this team.

    One sprint per role per sprint-number per project+group.
    e.g. "Sprint 0 — Backend", "Sprint 1 — Frontend", etc.

    Search order:
      1. Exact title + project + group match  → return as-is (don't auto-activate)
      2. Sprint-0 only: adopt an existing sprint whose title contains this role
         (Python-side filter — no .ilike()). Rename to canonical title.
      3. Create a fresh sprint. Only Sprint 0 is created as is_active=True;
         all others start as is_active=False and are activated by the caller
         once the pool is built and tasks are assigned.

    FIX 7: This function does NOT unconditionally activate a found sprint.
    Activation is the caller's explicit responsibility. This prevents
    a sprint from going active before its pool and initial tasks are ready.
    """
    title = _sprint_title(sprint_number, intern_role)

    # 1. Exact title match scoped to project + group
    query = (
        db.table("sprints")
        .select("*")
        .eq("project_id", project_id)
        .eq("title", title)
    )
    if group_id:
        query = query.eq("group_id", group_id)
    result = query.limit(1).execute()
    if result.data:
        return result.data[0]   # FIX 7: return without activating

    # 2. Sprint-0 only: adopt existing sprint whose title contains this role
    if sprint_number == 0:
        candidates_query = (
            db.table("sprints")
            .select("*")
            .eq("project_id", project_id)
        )
        if group_id:
            candidates_query = candidates_query.eq("group_id", group_id)
        candidates = candidates_query.execute().data or []

        role_needle = intern_role.replace("_", " ").lower()
        # Prefer active sprints first
        candidates.sort(key=lambda s: (0 if s.get("is_active") else 1))

        for existing in candidates:
            existing_title = (existing.get("title") or "").lower()
            if role_needle in existing_title:
                # Rename to canonical title; preserve current is_active state
                db.table("sprints").update({
                    "title": title,
                }).eq("id", existing["id"]).execute()
                logger.info(
                    f"[AdaptiveEngine] Adopted sprint '{existing['title']}' "
                    f"→ renamed to '{title}' project={project_id} role={intern_role}"
                )
                existing["title"] = title
                return existing

    # 3. Create fresh sprint
    today      = date.today()
    start_date = today + timedelta(weeks=sprint_number * 2)
    end_date   = start_date + timedelta(days=13)

    # FIX 7: only Sprint 0 starts active; caller activates higher sprints
    # after pool + tasks are ready.
    is_active_on_create = (sprint_number == 0)

    sprint = db.table("sprints").insert({
        "id":          str(uuid.uuid4()),
        "project_id":  project_id,
        "group_id":    group_id,
        "title":       title,
        "description": f"Adaptive sprint {sprint_number} for {_role_title(intern_role)} interns",
        "start_date":  start_date.isoformat(),
        "end_date":    end_date.isoformat(),
        "is_active":   is_active_on_create,
        "created_by":  created_by,
    }).execute()

    logger.info(
        f"[AdaptiveEngine] Created sprint '{title}' is_active={is_active_on_create} "
        f"project={project_id} group={group_id} role={intern_role}"
    )
    return sprint.data[0]


# ── Pool setup ─────────────────────────────────────────────────────────────────

def _reset_seeded_tasks_to_pool(
    sprint_id: str,
    project_id: str,
    group_id: str | None,
    intern_role: str,
    team_member_ids: list[str],
) -> None:
    """
    When seed data has pre-assigned tasks, unassign them back to the pool
    and stamp them with difficulty so the engine can drip them properly.

    Difficulty assignment for legacy tasks (no difficulty set):
      First 43% → easy, next 43% → medium, last 14% → hard
    """
    res = (
        db.table("tasks")
        .select("id, difficulty, title")
        .eq("sprint_id", sprint_id)
        .eq("project_id", project_id)
        .in_("assigned_to", team_member_ids)
        .execute()
    )
    seeded = res.data or []
    if not seeded:
        return

    logger.info(
        f"[AdaptiveEngine] Resetting {len(seeded)} seeded tasks to pool "
        f"sprint={sprint_id} role={intern_role}"
    )

    n_easy, n_medium, n_hard = _pool_split(len(seeded))
    difficulties = (
        ["easy"]   * n_easy +
        ["medium"] * n_medium +
        ["hard"]   * n_hard
    )
    while len(difficulties) < len(seeded):
        difficulties.append("medium")

    now = _now()
    for task, diff in zip(seeded, difficulties):
        existing_diff = task.get("difficulty") or diff
        db.table("tasks").update({
            "assigned_to": None,
            "difficulty":  existing_diff,
            "intern_role": intern_role,
            "status":      "todo",
            "updated_at":  now,
        }).eq("id", task["id"]).execute()

    logger.info(
        f"[AdaptiveEngine] Pool reset done sprint={sprint_id}: "
        f"{n_easy} easy, {n_medium} medium, {n_hard} hard"
    )


def _stamp_null_difficulty_tasks(tasks: list[dict]) -> None:
    """
    FIX 12: Stamp NULL-difficulty pool tasks with a round-robin distribution.

    When a sprint is seeded directly (bypassing the adaptive engine), tasks may
    have difficulty=NULL. _pick_from_pool queries WHERE difficulty=<tier>, so
    these tasks are invisible to it even though they exist in the pool.
    Distribution cycles: easy, easy, easy, medium, medium, medium, hard.
    """
    null_diff = [t for t in tasks if not t.get("difficulty")]
    if not null_diff:
        return
    cycle = ["easy", "easy", "easy", "medium", "medium", "medium", "hard"]
    now   = _now()
    for i, task in enumerate(null_diff):
        diff = cycle[i % len(cycle)]
        db.table("tasks").update({"difficulty": diff, "updated_at": now}).eq("id", task["id"]).execute()
        task["difficulty"] = diff
        logger.info(f"[AdaptiveEngine] FIX 12: stamped difficulty={diff} on task id={task['id']}")


def build_task_pool(
    project_id: str,
    group_id: str | None,
    sprint_id: str,
    intern_role: str,
    member_count: int,
) -> list[dict]:
    """
    Creates the unassigned task pool for a sprint.

    Pool size = ceil(member_count × 3.5), split ~43% easy / ~43% medium / ~14% hard.
    Example: 2 members → 7 tasks → 3 easy + 3 medium + 1 hard

    This function is IDEMPOTENT — safe to call multiple times.

    FIX 2  — Template tasks with NULL intern_role are stamped BEFORE insert
              so the NOT NULL constraint on tasks.intern_role is never violated.
    FIX 6  — Guard compares total tasks (assigned + unassigned) in sprint for
              this role, not just unassigned count.
    FIX 10 — Only inserts exactly need_easy + need_medium + need_hard tasks,
              correctly cycling through available templates.

    Sources (in priority order):
      1. Sprint already has tasks for this role → return unassigned subset.
      2. Template tasks (sprint_id IS NULL, assigned_to IS NULL, same project).
         Pass A: exact intern_role match.
         Pass B: NULL intern_role fallback (seed data) — stamp role before insert.
      3. Legacy seeded tasks: already assigned to team members in this sprint
         → unassign back to pool and stamp with difficulty.
    """
    pool_size = math.ceil(member_count * 3.5)

    # ── Fetch all tasks in this sprint for this role (assigned + unassigned) ──
    all_sprint_res = (
        db.table("tasks")
        .select("id, difficulty, assigned_to, status, intern_role")
        .eq("sprint_id", sprint_id)
        .eq("project_id", project_id)
        .execute()
    )
    all_sprint_raw = all_sprint_res.data or []

    # Stamp any NULL-role tasks in this sprint with the correct role
    # (handles seed data that was inserted without intern_role).
    now = _now()
    for row in all_sprint_raw:
        if not row.get("intern_role"):
            db.table("tasks").update({
                "intern_role": intern_role,
                "updated_at":  now,
            }).eq("id", row["id"]).execute()
            row["intern_role"] = intern_role
            logger.info(
                f"[AdaptiveEngine] build_task_pool: stamped NULL-role task "
                f"id={row['id']} sprint={sprint_id} as intern_role={intern_role}"
            )

    # Filter to only this role's tasks
    all_sprint_tasks = [
        t for t in all_sprint_raw if t.get("intern_role") == intern_role
    ]
    total_count = len(all_sprint_tasks)
    unassigned  = [t for t in all_sprint_tasks if t.get("assigned_to") is None]

    # FIX 6: guard against total tasks, not just unassigned.
    # FIX 12: Stamp NULL-difficulty tasks before returning so _pick_from_pool
    # can find them by exact difficulty match. Without this, seeded tasks with
    # difficulty=NULL are counted (triggering early return) but never picked.
    if total_count >= pool_size:
        _stamp_null_difficulty_tasks(unassigned)
        refreshed = (
            db.table("tasks")
            .select("id, difficulty, assigned_to, status, intern_role")
            .eq("sprint_id", sprint_id)
            .eq("project_id", project_id)
            .eq("intern_role", intern_role)
            .is_("assigned_to", "null")
            .execute()
        )
        logger.debug(
            f"[AdaptiveEngine] Pool already built sprint={sprint_id} role={intern_role} "
            f"(have {total_count} total ≥ need {pool_size})"
        )
        return refreshed.data or []

    # Calculate how many more of each difficulty are needed
    existing_by_diff: dict[str, int] = {"easy": 0, "medium": 0, "hard": 0}
    for row in all_sprint_tasks:
        d = row.get("difficulty") or "easy"
        if d in existing_by_diff:
            existing_by_diff[d] += 1

    n_easy, n_medium, n_hard = _pool_split(pool_size)
    need_easy   = max(0, n_easy   - existing_by_diff["easy"])
    need_medium = max(0, n_medium - existing_by_diff["medium"])
    need_hard   = max(0, n_hard   - existing_by_diff["hard"])
    still_needed = need_easy + need_medium + need_hard

    if still_needed == 0:
        return unassigned

    logger.info(
        f"[AdaptiveEngine] Building pool sprint={sprint_id} role={intern_role} "
        f"members={member_count} pool_size={pool_size} "
        f"(need e={need_easy} m={need_medium} h={need_hard})"
    )

    # ── Source 1: template tasks (sprint_id=NULL, assigned_to=NULL) ───────────
    def _find_templates(role_filter) -> list[dict]:
        q = (
            db.table("tasks")
            .select("*")
            .eq("project_id", project_id)
            .is_("assigned_to", "null")
            .is_("sprint_id", "null")
        )
        if role_filter is not None:
            q = q.eq("intern_role", role_filter)
        else:
            q = q.is_("intern_role", "null")
        return q.execute().data or []

    # Pass A: exact role match
    templates = _find_templates(intern_role)

    # Pass B: NULL role fallback (seed data without role stamped)
    if not templates:
        templates = _find_templates(None)
        if templates:
            logger.info(
                f"[AdaptiveEngine] Found {len(templates)} template tasks with NULL "
                f"intern_role for project={project_id} — stamping as role={intern_role}"
            )

    if templates:
        # Build the full difficulty list: need_easy easies, need_medium mediums, need_hard hards
        difficulties = (
            ["easy"]   * need_easy +
            ["medium"] * need_medium +
            ["hard"]   * need_hard
        )
        # FIX 10: cycle templates to fill exactly still_needed slots
        source_cycle = itertools.cycle(templates)
        now_ts       = _now()
        new_tasks    = []

        for diff in difficulties:
            t = next(source_cycle)
            # FIX 2: intern_role is stamped here (before insert), never NULL
            new_tasks.append({
                "id":          str(uuid.uuid4()),
                "project_id":  project_id,
                "group_id":    group_id,
                "sprint_id":   sprint_id,
                "intern_role": intern_role,        # guaranteed non-NULL
                "difficulty":  diff,
                "title":       t["title"],
                "description": t.get("description") or "",
                "priority":    t.get("priority") or "medium",
                "status":      "todo",
                "resources":   t.get("resources"),
                "task_doc":    t.get("task_doc"),
                "assigned_to": None,
                "created_at":  now_ts,
                "updated_at":  now_ts,
                "created_by":  None,
            })

        if new_tasks:
            db.table("tasks").insert(new_tasks).execute()
            logger.info(
                f"[AdaptiveEngine] Inserted {len(new_tasks)} pool tasks "
                f"(e={need_easy} m={need_medium} h={need_hard}) "
                f"sprint={sprint_id} role={intern_role}"
            )
        return unassigned + new_tasks

    # ── Source 2: legacy seeded tasks (already assigned to team members) ──────
    team_ids = _get_role_member_ids(project_id, group_id, intern_role)
    if team_ids:
        seeded_res = (
            db.table("tasks")
            .select("id, difficulty, title")
            .eq("sprint_id", sprint_id)
            .eq("project_id", project_id)
            .in_("assigned_to", team_ids)
            .execute()
        )
        seeded = seeded_res.data or []
        if seeded:
            _reset_seeded_tasks_to_pool(
                sprint_id=sprint_id,
                project_id=project_id,
                group_id=group_id,
                intern_role=intern_role,
                team_member_ids=team_ids,
            )
            refreshed = (
                db.table("tasks")
                .select("id, difficulty, assigned_to, status")
                .eq("sprint_id", sprint_id)
                .eq("project_id", project_id)
                .eq("intern_role", intern_role)
                .is_("assigned_to", "null")
                .execute()
            )
            return refreshed.data or []

    logger.warning(
        f"[AdaptiveEngine] No template or seeded tasks found for "
        f"sprint={sprint_id} role={intern_role} project={project_id}. Pool empty."
    )
    return unassigned


# ── Initial assignment ─────────────────────────────────────────────────────────

def assign_initial_tasks(
    user_id: str,
    sprint_id: str,
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> None:
    """
    Assign exactly 1 easy + 1 medium task to an intern at sprint start.
    Fully idempotent.

    FIX 1:  intern_role must be valid before calling _pick_from_pool.
    FIX 5:  Hard guard — intern already has ≥ 2 assigned tasks → skip.
    FIX 7b: Always ensures pool is built and stamped before picking.
    """
    if not intern_role or intern_role not in VALID_INTERN_ROLES:
        logger.error(
            f"[AdaptiveEngine] assign_initial_tasks: invalid intern_role "
            f"'{intern_role}' for user={user_id} — aborting"
        )
        return

    # Ensure pool exists and all tasks are role-stamped before picking
    member_count = _count_role_members(project_id, group_id, intern_role)
    build_task_pool(
        project_id=project_id,
        group_id=group_id,
        sprint_id=sprint_id,
        intern_role=intern_role,
        member_count=member_count,
    )

    already_res = (
        db.table("tasks")
        .select("id, difficulty")
        .eq("sprint_id", sprint_id)
        .eq("assigned_to", user_id)
        .execute()
    )
    already_rows  = already_res.data or []
    already_count = len(already_rows)

    # FIX 5: hard idempotency guard
    if already_count >= 2:
        logger.debug(
            f"[AdaptiveEngine] user={user_id} already has {already_count} tasks "
            f"in sprint={sprint_id} — skipping initial assignment"
        )
        return

    assigned_diffs = {row.get("difficulty") for row in already_rows}
    assigned       = []

    for diff in ("easy", "medium"):
        if diff in assigned_diffs:
            logger.debug(
                f"[AdaptiveEngine] user={user_id} already has a {diff} task "
                f"in sprint={sprint_id} — skipping"
            )
            continue
        task = _pick_from_pool(sprint_id, project_id, diff, intern_role)
        if task:
            _assign_task(task["id"], user_id, group_id)
            assigned.append(task)
        else:
            logger.warning(
                f"[AdaptiveEngine] No {diff} task available in pool "
                f"sprint={sprint_id} user={user_id} role={intern_role}"
            )

    if assigned:
        logger.info(
            f"[AdaptiveEngine] {len(assigned)} initial tasks → user={user_id} "
            f"sprint={sprint_id}: "
            + ", ".join(
                f"{t.get('difficulty')} '{t.get('title', '')[:40]}'"
                for t in assigned
            )
        )
    else:
        logger.warning(
            f"[AdaptiveEngine] assign_initial_tasks: no tasks assigned to "
            f"user={user_id} sprint={sprint_id} role={intern_role} — pool may be empty"
        )


# ── Pool pick & assign ─────────────────────────────────────────────────────────

def _pick_from_pool(
    sprint_id: str,
    project_id: str,
    difficulty: str,
    intern_role: str,
) -> dict | None:
    """
    Pick the first unassigned task of the given difficulty from the sprint pool.

    FIX 1:  intern_role filter always applied; returns None if role is falsy.
    FIX 9:  Empty-string guard — returns None immediately rather than querying
            the DB with intern_role="" which returns no rows silently.
    FIX 7b: NULL-role fallback: if no role-matched task found, picks a NULL-role
            task, stamps it, and returns it (handles seed data edge cases).
    """
    if not intern_role:
        logger.error(
            f"[AdaptiveEngine] _pick_from_pool called with empty intern_role "
            f"sprint={sprint_id} difficulty={difficulty}"
        )
        return None

    # Primary: exact role match
    res = (
        db.table("tasks")
        .select("id, difficulty, title, intern_role")
        .eq("sprint_id", sprint_id)
        .eq("project_id", project_id)
        .eq("difficulty", difficulty)
        .eq("intern_role", intern_role)
        .is_("assigned_to", "null")
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]

    # Fallback: NULL intern_role — stamp it before returning
    null_res = (
        db.table("tasks")
        .select("id, difficulty, title, intern_role")
        .eq("sprint_id", sprint_id)
        .eq("project_id", project_id)
        .eq("difficulty", difficulty)
        .is_("intern_role", "null")
        .is_("assigned_to", "null")
        .limit(1)
        .execute()
    )
    if null_res.data:
        task = null_res.data[0]
        db.table("tasks").update({
            "intern_role": intern_role,
            "updated_at":  _now(),
        }).eq("id", task["id"]).execute()
        task["intern_role"] = intern_role
        logger.info(
            f"[AdaptiveEngine] _pick_from_pool: stamped NULL-role task "
            f"id={task['id']} as intern_role={intern_role}"
        )
        return task

    return None


def _assign_task(task_id: str, user_id: str, group_id: str | None) -> None:
    db.table("tasks").update({
        "assigned_to": user_id,
        "group_id":    group_id,
        "status":      "todo",
        "updated_at":  _now(),
    }).eq("id", task_id).execute()
    logger.info(f"[AdaptiveEngine] Assigned task={task_id} → user={user_id}")


def _has_pending_tasks(user_id: str, sprint_id: str) -> bool:
    """True if the intern has any non-done tasks assigned in this sprint."""
    res = (
        db.table("tasks")
        .select("id")
        .eq("assigned_to", user_id)
        .eq("sprint_id", sprint_id)
        .neq("status", "done")
        .execute()
    )
    return bool(res.data)


# ── Team sprint advance ────────────────────────────────────────────────────────

def _advance_sprint_for_team(
    current_sprint: dict,
    project_id: str,
    group_id: str | None,
    intern_role: str,
    member_count: int,
    triggered_by: str,
) -> dict | None:
    """
    Advance the entire team to the next sprint when ALL their assigned tasks are done.

    FIX 3  — Race condition: after deactivating current sprint, re-check whether
              next sprint already exists and is active before creating a duplicate.
    FIX 8  — Sprint is activated ONLY after pool is built and initial tasks are
              assigned. This prevents the race window where an intern sees an active
              sprint with no tasks.

    Steps:
      1. Verify team sprint is complete (pre-check)
      2. Deactivate current sprint
      3. Race dedup: check if next sprint already exists and is active
      4. Get or create next sprint (created as is_active=False for N>0)
      5. Build pool for next sprint
      6. Assign 1 easy + 1 medium to every team member
      7. Activate next sprint  ← FIX 8: activation is last
      8. Notify all team members
    """
    current_number = _get_sprint_number(current_sprint.get("title", ""))
    if current_number is None:
        nums = re.findall(r"\d+", current_sprint.get("title", ""))
        current_number = int(nums[0]) if nums else 0
        logger.warning(
            f"[AdaptiveEngine] Could not parse sprint number from "
            f"'{current_sprint.get('title')}', assuming {current_number}"
        )

    if not _is_sprint_complete_for_team(
        current_sprint["id"], project_id, group_id, intern_role
    ):
        logger.info(
            f"[AdaptiveEngine] Sprint {current_sprint['id']} not complete "
            f"for team role={intern_role} group={group_id} — skipping advance"
        )
        return None

    next_number = current_number + 1
    next_title  = _sprint_title(next_number, intern_role)

    logger.info(
        f"[AdaptiveEngine] Team advance role={intern_role} group={group_id}: "
        f"sprint {current_number} → {next_number} (triggered by user={triggered_by})"
    )

    # Step 2: Deactivate current sprint
    db.table("sprints").update({"is_active": False}).eq("id", current_sprint["id"]).execute()

    # Step 3: Race condition dedup — another thread may have already advanced
    existing_next_q = (
        db.table("sprints")
        .select("*")
        .eq("project_id", project_id)
        .eq("title", next_title)
        .eq("is_active", True)
    )
    if group_id:
        existing_next_q = existing_next_q.eq("group_id", group_id)
    existing_next = existing_next_q.limit(1).execute()

    if existing_next.data:
        already_active = existing_next.data[0]
        logger.info(
            f"[AdaptiveEngine] Next sprint '{next_title}' already active "
            f"(race condition dedup) — ensuring tasks for user={triggered_by}"
        )
        assign_initial_tasks(
            triggered_by,
            already_active["id"],
            project_id,
            group_id,
            intern_role,
        )
        return already_active

    # Step 4: Get or create next sprint (is_active=False until tasks are ready)
    next_sprint = get_or_create_role_sprint(
        project_id=project_id,
        group_id=group_id,
        intern_role=intern_role,
        sprint_number=next_number,
        created_by=triggered_by,
    )

    # Step 5: Build pool
    build_task_pool(
        project_id=project_id,
        group_id=group_id,
        sprint_id=next_sprint["id"],
        intern_role=intern_role,
        member_count=member_count,
    )

    # Step 6: Assign initial tasks to every team member
    team_member_ids = _get_role_member_ids(project_id, group_id, intern_role)
    for member_id in team_member_ids:
        assign_initial_tasks(
            member_id,
            next_sprint["id"],
            project_id,
            group_id,
            intern_role,
        )

    # Step 7: Activate new sprint  ← FIX 8: activation is last
    db.table("sprints").update({"is_active": True}).eq("id", next_sprint["id"]).execute()
    next_sprint["is_active"] = True

    # Step 8: Notify all team members
    for member_id in team_member_ids:
        upsert_notification(
            user_id=member_id,
            key=f"sprint_advance_{next_sprint['id']}_{member_id}",
            type_="sprint_advance",
            title=f"🚀 Sprint {next_number} Unlocked!",
            body=(
                f"Your team completed Sprint {current_number}. "
                f"Sprint {next_number} is now live — new tasks assigned!"
            ),
            icon="🚀",
            href="/dashboard",
            count=1,
        )

    logger.info(
        f"[AdaptiveEngine] Sprint advanced: '{next_sprint['title']}' "
        f"id={next_sprint['id']} is_active=True role={intern_role}"
    )
    return next_sprint


# ── Main entry point ───────────────────────────────────────────────────────────

def on_task_done(user_id: str, task_id: str) -> dict:
    """
    Called whenever an intern marks a task as 'done'.

    FIX 1  — Returns early if intern_role is None/empty.
    FIX 11 — intern_role validated before any DB ops that require it.

    Flow:
      1. Resolve user's team context (role must be valid).
      2. Find which sprint the completed task belongs to.
      3. If intern still has non-done tasks → nothing to do yet.
      4. Compute performance score.
      5. Try to assign next pool task (tier-matched, then fallback tiers).
      6. Pool exhausted + whole team done → advance to next sprint.
      7. Pool exhausted but teammates still working → notify intern to wait.
    """
    ctx         = _get_user_context(user_id)
    project_id  = ctx["project_id"]
    group_id    = ctx["group_id"]
    intern_role = ctx["intern_role"]

    # FIX 11: bail out early if role is missing or invalid
    if not project_id:
        return {"action": "none", "reason": "no active project"}
    if not intern_role or intern_role not in VALID_INTERN_ROLES:
        logger.warning(
            f"[AdaptiveEngine] on_task_done: invalid intern_role '{intern_role}' "
            f"for user={user_id} — skipping adaptive assignment"
        )
        return {"action": "none", "reason": f"invalid intern_role: {intern_role}"}

    task_res = (
        db.table("tasks")
        .select("sprint_id")
        .eq("id", task_id)
        .limit(1)
        .execute()
    )
    if not task_res.data:
        return {"action": "none", "reason": "task not found"}

    sprint_id = task_res.data[0].get("sprint_id")
    if not sprint_id:
        return {"action": "none", "reason": "task has no sprint_id"}

    # Step 3: intern still has active tasks?
    if _has_pending_tasks(user_id, sprint_id):
        return {"action": "none", "reason": "intern still has active tasks"}

    # Step 4: compute performance score
    perf  = compute_performance_score(user_id, sprint_id)
    tier  = perf["difficulty_tier"]
    score = perf["performance_score"]

    logger.info(
        f"[AdaptiveEngine] on_task_done user={user_id} sprint={sprint_id} "
        f"score={score:.1f} tier={tier}"
    )

    # Step 5: try to assign next pool task (preferred tier, then fallbacks)
    tier_order = {
        "easy":   ["easy", "medium", "hard"],
        "medium": ["medium", "easy", "hard"],
        "hard":   ["hard", "medium", "easy"],
    }
    for try_tier in tier_order.get(tier, [tier]):
        pool_task = _pick_from_pool(sprint_id, project_id, try_tier, intern_role)
        if pool_task:
            _assign_task(pool_task["id"], user_id, group_id)
            upsert_notification(
                user_id=user_id,
                key=f"new_task_{pool_task['id']}",
                type_="new_task",
                title="📋 New Task Assigned",
                body=(
                    f"Score {score:.0f} → assigned {try_tier} task: "
                    f"\"{pool_task['title']}\""
                ),
                icon="📋",
                href="/dashboard",
                count=1,
            )
            return {
                "action":     "assigned",
                "task_id":    pool_task["id"],
                "task_title": pool_task["title"],
                "difficulty": try_tier,
                "score":      score,
            }

    # Pool exhausted — fetch sprint record
    sprint_res = (
        db.table("sprints")
        .select("*")
        .eq("id", sprint_id)
        .limit(1)
        .execute()
    )
    if not sprint_res.data:
        return {"action": "none", "reason": "sprint record not found"}

    current_sprint = sprint_res.data[0]

    # Step 6: check if whole team is done
    if not _is_sprint_complete_for_team(sprint_id, project_id, group_id, intern_role):
        upsert_notification(
            user_id=user_id,
            key=f"sprint_waiting_{sprint_id}_{user_id}",
            type_="sprint_waiting",
            title="⏳ Waiting for Teammates",
            body=(
                "You've finished all your tasks! "
                "Waiting for your teammates to complete the sprint."
            ),
            icon="⏳",
            href="/dashboard",
            count=1,
        )
        return {
            "action": "waiting",
            "reason": "pool exhausted; waiting for team sprint completion",
            "score":  score,
        }

    # Whole team done → advance
    member_count = _count_role_members(project_id, group_id, intern_role)
    next_sprint  = _advance_sprint_for_team(
        current_sprint=current_sprint,
        project_id=project_id,
        group_id=group_id,
        intern_role=intern_role,
        member_count=member_count,
        triggered_by=user_id,
    )

    if next_sprint:
        return {
            "action":           "sprint_advanced",
            "new_sprint_id":    next_sprint["id"],
            "new_sprint_title": next_sprint["title"],
            "score":            score,
        }

    return {"action": "none", "reason": "pool exhausted and could not advance sprint"}


# ── Sprint initialisation (called from projects.py on join) ───────────────────

def initialise_sprint_for_intern(
    user_id: str,
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> dict | None:
    """
    Called when an intern joins a project.

    One sprint track per role:
      frontend  → "Sprint 0 — Frontend"
      backend   → "Sprint 0 — Backend"
      tester    → "Sprint 0 — Tester"
      ui_ux     → "Sprint 0 — Ui Ux"

    FIX 1:  Validates intern_role before proceeding.
    FIX 7:  Sprint activated last, after pool + tasks are ready.
    FIX 8:  Activation order: create → build pool → assign tasks → activate.

    Steps:
      1. Validate intern_role.
      2. Count team members.
      3. Find or create Sprint 0 for this role.
      4. Build task pool (idempotent).
      5. Assign 2 initial tasks (1 easy + 1 medium) to this intern (idempotent).
      6. Ensure sprint is marked active.

    Safe to call multiple times — all operations are idempotent.
    """
    if not intern_role or intern_role not in VALID_INTERN_ROLES:
        logger.error(
            f"[AdaptiveEngine] initialise_sprint_for_intern: invalid "
            f"intern_role='{intern_role}' for user={user_id} project={project_id}"
        )
        return None

    member_count = _count_role_members(project_id, group_id, intern_role)

    # Step 3: find or create Sprint 0 for this role
    sprint = get_or_create_role_sprint(
        project_id=project_id,
        group_id=group_id,
        intern_role=intern_role,
        sprint_number=0,
        created_by=user_id,
    )

    # Step 4: build pool (idempotent)
    build_task_pool(
        project_id=project_id,
        group_id=group_id,
        sprint_id=sprint["id"],
        intern_role=intern_role,
        member_count=member_count,
    )

    # Step 5: assign 2 initial tasks
    assign_initial_tasks(user_id, sprint["id"], project_id, group_id, intern_role)

    # Step 6: FIX 7/8 — activate AFTER pool and tasks are ready
    if not sprint.get("is_active"):
        db.table("sprints").update({"is_active": True}).eq("id", sprint["id"]).execute()
        sprint["is_active"] = True
        logger.info(
            f"[AdaptiveEngine] Activated sprint '{sprint['title']}' "
            f"id={sprint['id']} after pool+tasks ready"
        )

    logger.info(
        f"[AdaptiveEngine] Sprint initialised sprint={sprint['id']} "
        f"'{sprint['title']}' user={user_id} role={intern_role} members={member_count}"
    )
    return sprint


# ── Recovery: fix interns stuck with no active sprint ─────────────────────────

def recover_intern_sprint(
    user_id: str,
    project_id: str,
    group_id: str | None,
    intern_role: str,
) -> dict:
    """
    Diagnose and fix an intern stuck in one of these broken states:

      State A — No sprint at all
        Fix: full initialise_sprint_for_intern

      State B — Sprint exists but is_active=False
        Fix: activate sprint, rebuild pool, assign initial tasks

      State C — Sprint active but intern has 0 tasks assigned
        Fix: rebuild pool, assign initial tasks

    Returns a dict describing what was detected and what action was taken.
    """
    if not intern_role or intern_role not in VALID_INTERN_ROLES:
        return {
            "status": "failed",
            "action_taken": f"invalid_intern_role:{intern_role}",
        }

    logger.info(
        f"[AdaptiveEngine] Recovery check user={user_id} "
        f"project={project_id} group={group_id} role={intern_role}"
    )

    role_needle = intern_role.replace("_", " ").lower()

    # ── Check 1: Does the intern have an active sprint? ────────────────────────
    active_q = (
        db.table("sprints")
        .select("*")
        .eq("project_id", project_id)
        .eq("is_active", True)
    )
    if group_id:
        active_q = active_q.eq("group_id", group_id)
    active_sprints = active_q.execute().data or []

    active_sprint = next(
        (s for s in active_sprints if role_needle in (s.get("title") or "").lower()),
        None,
    )

    if active_sprint:
        tasks_res = (
            db.table("tasks")
            .select("id")
            .eq("sprint_id", active_sprint["id"])
            .eq("assigned_to", user_id)
            .execute()
        )
        if tasks_res.data:
            return {
                "status":        "healthy",
                "sprint_id":     active_sprint["id"],
                "sprint_title":  active_sprint["title"],
                "tasks_assigned": len(tasks_res.data),
                "action_taken":  "none",
            }

        # State C: active sprint but intern has no tasks
        logger.info(
            f"[AdaptiveEngine] Recovery State C: active sprint but 0 tasks "
            f"for user={user_id} sprint={active_sprint['id']}"
        )
        member_count = _count_role_members(project_id, group_id, intern_role)
        build_task_pool(
            project_id=project_id,
            group_id=group_id,
            sprint_id=active_sprint["id"],
            intern_role=intern_role,
            member_count=member_count,
        )
        assign_initial_tasks(
            user_id, active_sprint["id"], project_id, group_id, intern_role
        )
        return {
            "status":       "recovered",
            "sprint_id":    active_sprint["id"],
            "sprint_title": active_sprint["title"],
            "action_taken": "assigned_initial_tasks_to_existing_sprint",
        }

    # ── Check 2: Does an inactive sprint exist? ────────────────────────────────
    inactive_q = (
        db.table("sprints")
        .select("*")
        .eq("project_id", project_id)
        .eq("is_active", False)
    )
    if group_id:
        inactive_q = inactive_q.eq("group_id", group_id)
    inactive_sprints = inactive_q.execute().data or []

    inactive_sprint = next(
        (s for s in inactive_sprints if role_needle in (s.get("title") or "").lower()),
        None,
    )

    if inactive_sprint:
        # State B: sprint exists but not active
        logger.info(
            f"[AdaptiveEngine] Recovery State B: inactive sprint found "
            f"for user={user_id} sprint={inactive_sprint['id']} — activating"
        )
        member_count = _count_role_members(project_id, group_id, intern_role)
        build_task_pool(
            project_id=project_id,
            group_id=group_id,
            sprint_id=inactive_sprint["id"],
            intern_role=intern_role,
            member_count=member_count,
        )
        assign_initial_tasks(
            user_id, inactive_sprint["id"], project_id, group_id, intern_role
        )
        # Activate last, after pool + tasks are ready (FIX 8)
        db.table("sprints").update({"is_active": True}).eq("id", inactive_sprint["id"]).execute()
        return {
            "status":       "recovered",
            "sprint_id":    inactive_sprint["id"],
            "sprint_title": inactive_sprint["title"],
            "action_taken": "activated_sprint_and_assigned_tasks",
        }

    # State A: no sprint at all — full initialise
    logger.info(
        f"[AdaptiveEngine] Recovery State A: no sprint found — "
        f"running full initialise for user={user_id} role={intern_role}"
    )
    sprint = initialise_sprint_for_intern(
        user_id=user_id,
        project_id=project_id,
        group_id=group_id,
        intern_role=intern_role,
    )
    if not sprint:
        return {
            "status":       "failed",
            "action_taken": "initialise_sprint_for_intern_returned_none",
        }
    return {
        "status":       "recovered",
        "sprint_id":    sprint["id"],
        "sprint_title": sprint["title"],
        "action_taken": "full_sprint_initialisation",
    }