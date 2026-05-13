"""
app/routers/sim_mode.py
─────────────────────────────────────────────────────────────────────────────
Simulation Mode — QA Bug Flood
Simulates: "QA team reports multiple issues simultaneously"

What this does:
  1. Fetches all interns in the project / group
  2. Generates 6 realistic ShopSphere bug tickets mapped to the right
     intern roles (frontend, backend, devops, tester, etc.)
  3. Inserts tickets into the `tickets` table (from_group = QA group → to_group)
  4. Reopens one in-progress / done task per intern by setting status → 'todo'
  5. Pushes a notification to every intern
  6. Returns a structured result so the frontend can render a live flood log

Endpoint: POST /api/sim/qa-bug-flood
Auth:      any logged-in user (admin / mentor preferred — enforce in UI)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid
import logging
from datetime import datetime, timezone

from app.core.database import db, supabase_admin
from app.core.auth import get_current_user
from app.routers.notifications import upsert_notification

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sim", tags=["Simulation"])


# ─── ShopSphere-specific bug templates ────────────────────────────────────────
# Each bug targets a specific intern_role so tickets feel realistic.

SHOPSPHERE_BUGS = [
    {
        "title": "🛒 Cart total miscalculates when discount coupon applied",
        "description": (
            "QA REPORT — Severity: HIGH\n\n"
            "Steps to reproduce:\n"
            "1. Add 3 items totalling ₹2,500 to cart\n"
            "2. Apply coupon code SAVE20 (20% off)\n"
            "3. Observe cart total\n\n"
            "Expected: ₹2,000\n"
            "Actual: ₹2,480 (discount only applied to first item)\n\n"
            "Affected component: CartSummary.jsx, discount-engine utility\n"
            "Browser: Chrome 124, Safari 17\n"
            "Frequency: 100% reproducible"
        ),
        "type": "bug",
        "priority": "high",
        "target_role": "frontend",
    },
    {
        "title": "⚙️ /api/orders endpoint returns 500 on concurrent checkouts",
        "description": (
            "QA REPORT — Severity: CRITICAL\n\n"
            "Steps to reproduce:\n"
            "1. Simulate 10 concurrent POST /api/orders requests (same product_id)\n"
            "2. Observe response codes\n\n"
            "Expected: All requests return 200 with unique order_id\n"
            "Actual: 3-4 requests return 500 — DB unique constraint violation on order_number\n\n"
            "Stack trace excerpt:\n"
            "  UniqueViolation: duplicate key value violates unique constraint 'orders_order_number_key'\n\n"
            "Fix needed: atomic sequence or UUID-based order_number generation\n"
            "Env: Production FastAPI + Supabase"
        ),
        "type": "bug",
        "priority": "high",
        "target_role": "backend",
    },
    {
        "title": "📱 Product image carousel breaks on mobile viewport < 375px",
        "description": (
            "QA REPORT — Severity: MEDIUM\n\n"
            "Steps to reproduce:\n"
            "1. Open product detail page on device with 360px viewport (Galaxy S8)\n"
            "2. Swipe through product images\n\n"
            "Expected: Smooth carousel with thumbnail dots\n"
            "Actual: Carousel overflows container; dots disappear; last image not reachable\n\n"
            "Affected file: ProductGallery.jsx\n"
            "CSS issue: fixed 380px min-width on .carousel-track\n"
            "Devices affected: Galaxy S8, Moto G4, older iPhones (SE gen 1)"
        ),
        "type": "bug",
        "priority": "medium",
        "target_role": "frontend",
    },
    {
        "title": "🔐 Checkout page accessible without auth — orders created anonymously",
        "description": (
            "QA REPORT — Severity: CRITICAL\n\n"
            "Steps to reproduce:\n"
            "1. Open /checkout in incognito (no session cookie)\n"
            "2. Fill in address and click 'Place Order'\n\n"
            "Expected: Redirect to /login with return_url=/checkout\n"
            "Actual: Order is created with user_id = null; no error shown\n\n"
            "Security impact: Anonymous orders pollute DB, bypass fraud checks\n"
            "Fix: Add auth guard on both route level AND API /api/orders POST\n"
            "Related: Missing RLS policy on orders table for anon role"
        ),
        "type": "security",
        "priority": "high",
        "target_role": "backend",
    },
    {
        "title": "🚀 Search API p95 latency exceeds 4s — missing index on products.name",
        "description": (
            "QA REPORT — Severity: HIGH\n\n"
            "Load test results (k6, 50 VUs, 2 min):\n"
            "  GET /api/products?search=shirt\n"
            "  p50: 340ms | p95: 4,200ms | p99: 8,100ms\n\n"
            "Root cause identified:\n"
            "  EXPLAIN ANALYZE shows sequential scan on products table (82k rows)\n"
            "  Missing: CREATE INDEX idx_products_name ON products USING gin(to_tsvector('english', name));\n\n"
            "Also: N+1 query in product listing — fetching category separately per row\n"
            "Recommend: Add composite index + join category in single query"
        ),
        "type": "performance",
        "priority": "high",
        "target_role": "devops",
    },
    {
        "title": "🧾 Order confirmation email not sent when payment gateway returns PENDING",
        "description": (
            "QA REPORT — Severity: MEDIUM\n\n"
            "Steps to reproduce:\n"
            "1. Complete checkout with test card (Razorpay PENDING simulation)\n"
            "2. Wait 60 seconds for webhook\n\n"
            "Expected: Email sent immediately with 'Payment pending' status\n"
            "Actual: No email sent; order shows 'pending' in DB but user is unaware\n\n"
            "Root cause: Email trigger only fires on payment_status = 'success'\n"
            "Fix needed:\n"
            "  - Trigger email on PENDING with appropriate messaging\n"
            "  - Add retry mechanism for webhook failures (currently no retry queue)\n"
            "Affected service: notification-service / email-worker"
        ),
        "type": "bug",
        "priority": "medium",
        "target_role": "backend",
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(res) -> list:
    return res.data if res.data else []


# ─── Request / Response models ────────────────────────────────────────────────

class BugFloodRequest(BaseModel):
    project_id: str
    group_id: str                    # The QA / triggering group
    sprint_id: Optional[str] = None  # If present, reopen tasks from this sprint


class BugFloodResult(BaseModel):
    tickets_created: int
    tasks_reopened: int
    interns_notified: int
    tickets: list
    reopened_tasks: list
    log: list[str]


# ─── Main endpoint ────────────────────────────────────────────────────────────

@router.post("/qa-bug-flood", response_model=BugFloodResult)
async def run_qa_bug_flood(
    body: BugFloodRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Trigger the QA Bug Flood simulation for ShopSphere.

    - Creates 6 realistic bug tickets across intern roles
    - Reopens tasks (sets status → todo) to simulate bug-driven regression
    - Notifies all project interns
    """
    log: list[str] = []
    created_tickets = []
    reopened_tasks = []

    # ── 1. Fetch all interns in the project ───────────────────────────────────
    members_res = (
        supabase_admin.table("group_members")
        .select("user_id, intern_role, group_id")
        .eq("group_id", body.group_id)
        .execute()
    )
    members = _safe(members_res)

    if not members:
        # Fallback: fetch all interns assigned to the project
        profiles_res = (
            supabase_admin.table("profiles")
            .select("id, name, intern_role")
            .eq("project_id", body.project_id)
            .eq("role", "intern")
            .execute()
        )
        profiles = _safe(profiles_res)
        members = [{"user_id": p["id"], "intern_role": p.get("intern_role"), "group_id": body.group_id} for p in profiles]

    log.append(f"👥 Found {len(members)} intern(s) in group")

    # ── 2. Create 6 bug tickets ───────────────────────────────────────────────
    now = _now()
    for bug in SHOPSPHERE_BUGS:
        ticket_id = str(uuid.uuid4())
        ticket = {
            "id":            ticket_id,
            "title":         bug["title"],
            "description":   bug["description"],
            "type":          bug["type"],
            "priority":      bug["priority"],
            "status":        "open",
            "project_id":    body.project_id,
            "from_group_id": body.group_id,
            "to_group_id":   body.group_id,  # same group (role-virtual teams)
            "created_by":    current_user["id"],
            "created_at":    now,
            "updated_at":    now,
        }
        try:
            res = supabase_admin.table("tickets").insert(ticket).execute()
            if res.data:
                created_tickets.append(res.data[0])
                log.append(f"🎫 Ticket created: [{bug['priority'].upper()}] {bug['title'][:60]}…")
            else:
                log.append(f"⚠️  Failed to insert ticket: {bug['title'][:40]}")
        except Exception as e:
            logger.error(f"sim_mode ticket insert error: {e}")
            log.append(f"❌ Error inserting ticket: {e}")

    # ── 3. Reopen tasks — find in_progress/done tasks and push back to 'todo' ─
    task_query = (
        supabase_admin.table("tasks")
        .select("id, title, status, assigned_to, intern_role")
        .eq("project_id", body.project_id)
        .in_("status", ["in_progress", "done", "review"])
    )
    if body.sprint_id:
        task_query = task_query.eq("sprint_id", body.sprint_id)

    tasks_res = task_query.limit(6).execute()
    tasks_to_reopen = _safe(tasks_res)

    for task in tasks_to_reopen:
        try:
            supabase_admin.table("tasks").update({
                "status":          "todo",
                "previous_status": task["status"],
                "feedback":        (
                    "⚠️ [SIM] Task reopened — QA Bug Flood triggered a regression. "
                    "Please review the related bug tickets and fix before marking done again."
                ),
                "updated_at":      now,
            }).eq("id", task["id"]).execute()
            reopened_tasks.append(task)
            log.append(f"🔄 Task reopened: '{task['title'][:50]}' (was {task['status']})")
        except Exception as e:
            logger.error(f"sim_mode task reopen error: {e}")
            log.append(f"❌ Error reopening task {task['id']}: {e}")

    # ── 4. Notify all interns ─────────────────────────────────────────────────
    notified = set()
    for member in members:
        uid = member.get("user_id")
        if not uid or uid in notified:
            continue
        try:
            upsert_notification(
                user_id=uid,
                key=f"sim_bug_flood_{body.project_id}",
                type_="ticket",
                title="🚨 QA Bug Flood — 6 new bug reports filed!",
                body=(
                    f"{len(created_tickets)} bugs reported by QA. "
                    f"{len(reopened_tasks)} task(s) reopened. "
                    "Check your tickets and sprint board immediately."
                ),
                icon="🐛",
                href="/dashboard/ticket",
                count=len(created_tickets),
            )
            notified.add(uid)
            log.append(f"🔔 Notified intern {uid[:8]}…")
        except Exception as e:
            logger.error(f"sim_mode notify error for {uid}: {e}")
            log.append(f"⚠️  Notification failed for {uid[:8]}: {e}")

    log.append(
        f"✅ Bug Flood complete — {len(created_tickets)} tickets · "
        f"{len(reopened_tasks)} tasks reopened · {len(notified)} interns notified"
    )

    return BugFloodResult(
        tickets_created=len(created_tickets),
        tasks_reopened=len(reopened_tasks),
        interns_notified=len(notified),
        tickets=created_tickets,
        reopened_tasks=reopened_tasks,
        log=log,
    )