"""
backend/app/routers/client_requirement_change.py
────────────────────────────────────────────────
Client Requirement Change Mode — E-Commerce Project

When triggered:
  1. AI Mentor notification is sent to the intern with a full structured
     breakdown: what the client wants, priority, expected time, what NOT
     to break.
  2. A ticket is automatically raised for the intern's team
     (to_group_id = user's own group → appears as *incoming* on the ticket board).
  3. A new task is created and assigned to the intern (marked with
     mid_sprint_change_reason="client_requirement_change" so the Kanban
     board renders it with a purple highlight).
     Task is added to the user's OWN active sprint (resolved via group_id
     first, then project_id fallback).
  4. Teammates (same group_id AND same intern_role) get a brief "heads-up"
     notification.

Endpoint:
  POST /api/client-requirement-change/trigger
    Immediately triggers the mode — no delay (unlike mid-sprint change).

  GET  /api/client-requirement-change/scenarios
    Lists available scenarios for the user's role (dev / debug helper).
"""

import logging
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.auth import get_current_user
from app.core.database import supabase_admin
from app.routers.notifications import upsert_notification
from app.routers.tasks import _resolve_active_sprint_for_user

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/client-requirement-change",
    tags=["Client Requirement Change"],
)

# ─── Scenarios by role ────────────────────────────────────────────────────────
# Supported CCR roles: frontend, backend, ui_ux, tester
# Each role has exactly 2 scenarios so a random one is picked each time.

_SCENARIOS: dict[str, list[dict]] = {

    # ── FRONTEND ──────────────────────────────────────────────────────────────
    "frontend": [
        {
            "id": "ccr_fe_001",
            "title": "Add Product Quick-View Modal",
            "client_wants": (
                "Customers must be able to preview product images, price, and "
                "variants in a modal overlay on the catalogue page — without "
                "navigating away — to reduce catalogue bounce rate."
            ),
            "priority": "high",
            "expected_time": "2 days",
            "expected_time_days": 2,
            "avoid_breaking": [
                "Product listing page performance (LCP must stay < 2.5s)",
                "Existing click-to-product-detail navigation",
                "Cart state and add-to-cart flow",
                "Mobile responsiveness of the product grid",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Implement a Quick-View modal on the product catalogue. When a user "
                "hovers or clicks 'Quick View' on any product card, a modal appears "
                "showing: product images (with thumbnail strip), title, price, variant "
                "selector (size/colour), stock count, and an 'Add to Cart' button.\n\n"
                "⚡ Priority: HIGH — Client demo in 2 days.\n\n"
                "⏰ Expected delivery: 2 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Product listing LCP (< 2.5s)\n"
                "• Click-to-detail navigation (modal is additive, not a replacement)\n"
                "• Cart state and add-to-cart logic\n"
                "• Mobile product grid layout\n\n"
                "📋 Acceptance Criteria:\n"
                "1. 'Quick View' button visible on product card hover\n"
                "2. Modal opens with animation < 300ms\n"
                "3. Variant selection updates price/stock live\n"
                "4. 'Add to Cart' inside modal works correctly\n"
                "5. Accessible: Escape closes modal, focus is trapped\n"
                "6. Mobile: modal renders as a bottom sheet"
            ),
            "ticket_title": "Frontend: Product Quick-View Modal",
            "ticket_description": (
                "Client requested a Quick-View modal for the product catalogue. "
                "Frontend team to build the modal component and wire up variant "
                "selection + cart integration. Coordinate with backend if a dedicated "
                "product-detail endpoint is needed."
            ),
        },
        {
            "id": "ccr_fe_002",
            "title": "Checkout — Multi-Step Progress Stepper",
            "client_wants": (
                "Checkout drop-off is high. Add a visual 4-step progress indicator "
                "(Cart → Address → Payment → Confirmation) so users always know "
                "where they are and can go back without losing data."
            ),
            "priority": "medium",
            "expected_time": "3 days",
            "expected_time_days": 3,
            "avoid_breaking": [
                "Form validation and field state across steps",
                "Payment gateway callbacks (Razorpay/Stripe)",
                "Order summary sidebar on every step",
                "Browser back-button behaviour",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Redesign the checkout page with a clear 4-step stepper: Cart Review → "
                "Shipping Address → Payment → Order Confirmation. Each step must "
                "validate before allowing progression. Users must be able to go back "
                "to previous steps without losing their data.\n\n"
                "⚡ Priority: MEDIUM — UX improvement sprint.\n\n"
                "⏰ Expected delivery: 3 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Form field state across step transitions\n"
                "• Payment gateway (Razorpay/Stripe) callback handling\n"
                "• Order summary sidebar must stay visible on all steps\n"
                "• Browser back button must be handled gracefully\n\n"
                "📋 Acceptance Criteria:\n"
                "1. Active step is visually highlighted\n"
                "2. Completed steps show a checkmark\n"
                "3. 'Back' returns to previous step without data loss\n"
                "4. 'Next' is disabled until current step validates\n"
                "5. Stepper collapses to a compact view on mobile"
            ),
            "ticket_title": "Frontend: Checkout Multi-Step Stepper",
            "ticket_description": (
                "Checkout page needs a multi-step progress stepper to reduce drop-off. "
                "Frontend to redesign layout. Backend team to confirm if a new "
                "order-draft endpoint is needed to persist partial checkout state."
            ),
        },
    ],

    # ── BACKEND ───────────────────────────────────────────────────────────────
    "backend": [
        {
            "id": "ccr_be_001",
            "title": "Products API — Server-Side Search & Filters",
            "client_wants": (
                "The catalogue is slow with 500+ products. Add server-side search "
                "with filters: text search, category, price range, ratings, and "
                "in-stock only. Results must be sortable."
            ),
            "priority": "high",
            "expected_time": "2 days",
            "expected_time_days": 2,
            "avoid_breaking": [
                "Existing GET /api/products response shape",
                "Pagination params (page, limit) must work with filters",
                "Admin dashboard product management APIs",
                "Cart and order references to product IDs",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Add server-side search and filtering to the products API. Supported "
                "filters: ?search=, ?category=, ?min_price=, ?max_price=, "
                "?min_rating=, ?in_stock=true. Results sortable by: price_asc, "
                "price_desc, rating, newest.\n\n"
                "⚡ Priority: HIGH — Performance issue affecting live demo.\n\n"
                "⏰ Expected delivery: 2 working days\n\n"
                "🚫 Do NOT break:\n"
                "• GET /api/products response shape (add filters as optional params)\n"
                "• Pagination (page, limit) must still work alongside filters\n"
                "• Admin dashboard product management routes\n"
                "• Cart/order service references to product IDs\n\n"
                "📋 Acceptance Criteria:\n"
                "1. GET /api/products?search=shoes&category=footwear&min_price=500\n"
                "2. Sorting via ?sort=price_asc | price_desc | rating | newest\n"
                "3. Response includes total count for pagination metadata\n"
                "4. DB indexes added where needed for query performance\n"
                "5. No breaking change to existing API consumers"
            ),
            "ticket_title": "Backend: Server-Side Product Search & Filter API",
            "ticket_description": (
                "Products API needs server-side search + filter support. Backend to "
                "add optional query params to GET /api/products. Coordinate with "
                "frontend on param naming. DBA to review index strategy for large "
                "product tables."
            ),
        },
        {
            "id": "ccr_be_002",
            "title": "Order Status Webhook & Email Notification",
            "client_wants": (
                "Every order status change (confirmed → shipped → delivered / "
                "cancelled) must trigger an HTML email to the customer AND a webhook "
                "POST to the logistics partner's endpoint."
            ),
            "priority": "high",
            "expected_time": "3 days",
            "expected_time_days": 3,
            "avoid_breaking": [
                "PATCH /api/orders/{id}/status response contract",
                "Admin order management dashboard",
                "Existing order history GET endpoints",
                "Payment refund flow on cancellation",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "On every order status change: (1) send an HTML email to the customer "
                "using a branded template, and (2) POST a webhook to the logistics "
                "partner URL with order JSON payload.\n\n"
                "⚡ Priority: HIGH — Client goes live in 5 days.\n\n"
                "⏰ Expected delivery: 3 working days\n\n"
                "🚫 Do NOT break:\n"
                "• PATCH /api/orders/{id}/status response contract\n"
                "• Admin order management pages\n"
                "• GET /api/orders history endpoints\n"
                "• Refund flow when status → cancelled\n\n"
                "📋 Acceptance Criteria:\n"
                "1. Email dispatched async (background task, non-blocking)\n"
                "2. Webhook includes: order_id, status, customer_id, items, updated_at\n"
                "3. Webhook failures are logged and retried (max 3 attempts)\n"
                "4. Email template: HTML + plain-text fallback\n"
                "5. Unit tests for status transition + notification trigger logic"
            ),
            "ticket_title": "Backend: Order Status Email + Webhook Integration",
            "ticket_description": (
                "Order notifications needed: async email on status change + webhook "
                "to logistics partner. Backend to add async dispatch after status "
                "update. DevOps to configure SMTP/SendGrid credentials and "
                "WEBHOOK_URL in env vars."
            ),
        },
    ],

    # ── UI/UX ─────────────────────────────────────────────────────────────────
    "ui_ux": [
        {
            "id": "ccr_ux_001",
            "title": "Mobile Bottom Navigation Bar",
            "client_wants": (
                "Mobile UX review found users struggle to find categories and cart. "
                "Add a sticky bottom nav bar on mobile: Home, Categories, Search, "
                "Cart (with badge), Profile."
            ),
            "priority": "high",
            "expected_time": "2 days",
            "expected_time_days": 2,
            "avoid_breaking": [
                "Desktop header navigation (bottom nav is mobile-only ≤768px)",
                "Cart item count badge real-time updates",
                "Existing header on desktop",
                "Deep-link routing (/product/:id, /category/:slug)",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Add a sticky bottom navigation bar for mobile (max-width: 768px) with "
                "5 tabs: Home, Categories, Search (opens full-screen overlay), Cart "
                "(live item count badge), Profile. Active tab highlighted with brand "
                "colour.\n\n"
                "⚡ Priority: HIGH — Mobile conversion is 40% below desktop.\n\n"
                "⏰ Expected delivery: 2 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Desktop header navigation\n"
                "• Cart badge must reflect real-time cart count\n"
                "• All deep-link routes must work from bottom nav\n"
                "• Safe-area padding for iPhone notch/home bar\n\n"
                "📋 Acceptance Criteria:\n"
                "1. Bottom nav visible only at ≤768px\n"
                "2. Active route tab highlighted\n"
                "3. Cart tab shows live item count badge\n"
                "4. Search opens full-screen overlay\n"
                "5. iPhone safe-area padding applied"
            ),
            "ticket_title": "UI/UX: Mobile Bottom Navigation",
            "ticket_description": (
                "Mobile bottom navigation bar required for e-commerce app. UX team "
                "to design and implement component. Backend to confirm cart count "
                "API latency. Coordinate on routing strategy."
            ),
        },
        {
            "id": "ccr_ux_002",
            "title": "Redesign Empty States & Onboarding Tooltips",
            "client_wants": (
                "New users drop off when they see blank pages (empty cart, no orders, "
                "no wishlist). Each empty state must have a helpful illustration, "
                "copy, and a clear CTA. Also add first-time tooltips for key actions."
            ),
            "priority": "medium",
            "expected_time": "3 days",
            "expected_time_days": 3,
            "avoid_breaking": [
                "Existing page routing and layout structure",
                "Cart and order state logic (empty state is purely presentational)",
                "Accessibility — tooltips must be keyboard-dismissible",
                "Dark-mode colour tokens",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Design and implement empty state screens for: Cart (empty), Order "
                "History (no orders yet), Wishlist (nothing saved), and Search (no "
                "results). Each needs an illustration, supportive copy, and a primary "
                "CTA button. Add a one-time tooltip walkthrough for first-time users "
                "on the home and product pages.\n\n"
                "⚡ Priority: MEDIUM — Onboarding conversion fix.\n\n"
                "⏰ Expected delivery: 3 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Page routing and layout\n"
                "• Cart/order state — empty state is purely UI\n"
                "• Tooltips must be dismissible via keyboard (Escape)\n"
                "• Dark-mode colours must use existing design tokens\n\n"
                "📋 Acceptance Criteria:\n"
                "1. 4 empty state screens with illustration + CTA\n"
                "2. Tooltip shown once per session, stored in localStorage\n"
                "3. Tooltip dismissed on Escape or outside click\n"
                "4. Illustrations are SVG (not raster) for crisp rendering\n"
                "5. All copy reviewed and approved by PM before merge"
            ),
            "ticket_title": "UI/UX: Empty States & Onboarding Tooltips",
            "ticket_description": (
                "Empty state and tooltip redesign needed to improve new-user "
                "retention. UI/UX team to create illustration assets and implement "
                "components. Frontend to integrate tooltip dismissal logic. "
                "PM to sign off on copy before implementation."
            ),
        },
    ],

    # ── TESTER / QA ───────────────────────────────────────────────────────────
    "tester": [
        {
            "id": "ccr_te_001",
            "title": "E2E Test Coverage for Checkout Flow",
            "client_wants": (
                "Before go-live, all checkout edge cases must be covered by automated "
                "E2E tests: happy path, failed payments, out-of-stock blocking, and "
                "coupon code validation."
            ),
            "priority": "critical",
            "expected_time": "2 days",
            "expected_time_days": 2,
            "avoid_breaking": [
                "Existing unit test suite (no component logic changes)",
                "CI/CD pipeline test runner configuration",
                "Payment sandbox environment credentials",
                "DB seeding scripts",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Write comprehensive E2E tests (Playwright/Cypress) for the checkout "
                "flow: happy path, failed payment, out-of-stock block, coupon code "
                "validation, and address form edge cases.\n\n"
                "⚡ Priority: CRITICAL — Go-live is blocked without test sign-off.\n\n"
                "⏰ Expected delivery: 2 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Existing unit test suite\n"
                "• CI pipeline configuration\n"
                "• Payment sandbox (test credentials only)\n"
                "• DB seeding scripts (add fixtures, don't modify existing)\n\n"
                "📋 Acceptance Criteria:\n"
                "1. Happy path: cart → checkout → mock payment → confirmation\n"
                "2. Failed payment: error shown, order NOT created\n"
                "3. Out-of-stock: 'Add to Cart' disabled with message\n"
                "4. Valid coupon applies discount; invalid shows error\n"
                "5. All tests pass in CI against staging"
            ),
            "ticket_title": "QA: E2E Checkout Test Suite",
            "ticket_description": (
                "E2E tests needed for checkout before launch. QA team to write specs "
                "in Playwright/Cypress. Frontend to expose stable test selectors. "
                "Backend to provide test order/coupon seed data."
            ),
        },
        {
            "id": "ccr_te_002",
            "title": "API Contract & Regression Test Suite",
            "client_wants": (
                "Client discovered a silent regression: a frontend deploy broke the "
                "order API response without any test catching it. Build a contract "
                "test suite for all critical API endpoints so regressions are caught "
                "in CI before merge."
            ),
            "priority": "high",
            "expected_time": "3 days",
            "expected_time_days": 3,
            "avoid_breaking": [
                "Current CI pipeline (tests must be added as a new step, not replace)",
                "Staging environment DB (use isolated test fixtures)",
                "PR review workflow — contract tests must run on every PR",
                "Existing Postman collections (can be converted, not deleted)",
            ],
            "task_description": (
                "🤖 [AI MENTOR — CLIENT CHANGE REQUEST]\n\n"
                "📣 What the client wants:\n"
                "Implement API contract tests (Pact / Supertest / pytest-httpx) for: "
                "POST /api/orders, GET /api/products, POST /api/cart/add, "
                "POST /api/auth/login. Tests must validate response schema, status "
                "codes, and required fields. Run automatically on every PR.\n\n"
                "⚡ Priority: HIGH — Silent regression reached production.\n\n"
                "⏰ Expected delivery: 3 working days\n\n"
                "🚫 Do NOT break:\n"
                "• Existing CI pipeline — add contract tests as a new parallel step\n"
                "• Staging DB — use isolated fixtures, no prod data\n"
                "• Existing Postman collections — migrate, don't delete\n"
                "• PR workflow — tests must complete within 5 minutes\n\n"
                "📋 Acceptance Criteria:\n"
                "1. Contract tests for 4 critical endpoints\n"
                "2. Schema validation: required fields, types, nullable rules\n"
                "3. Tests run in under 5 min in CI\n"
                "4. Failing contract test blocks PR merge\n"
                "5. README updated with how to run tests locally"
            ),
            "ticket_title": "QA: API Contract & Regression Test Suite",
            "ticket_description": (
                "API contract tests required after silent regression in production. "
                "QA team to set up contract testing framework and cover critical "
                "endpoints. Backend to provide schema documentation. DevOps to "
                "integrate as a new CI pipeline step."
            ),
        },
    ],
}

# Aliases: map variant role names → canonical keys
_ROLE_ALIAS = {
    "fullstack": "backend",  # fullstack gets backend scenarios as closest match
    "devops":    "backend",
    "design":    "ui_ux",
    "default":   "frontend",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_role(raw_role: str) -> str:
    """Map any intern_role to a scenario bucket key."""
    if raw_role in _SCENARIOS:
        return raw_role
    return _ROLE_ALIAS.get(raw_role, "frontend")


def _get_user_context(user_id: str) -> dict:
    """
    Returns: { role, project_id, group_id, sprint_id }
    Sprint resolution order:
      1. Active sprint scoped to user's group_id  (most precise)
      2. Active sprint scoped to project_id       (fallback)
    All fields may be None if not found.
    """
    ctx: dict = {"role": "frontend", "project_id": None, "group_id": None, "sprint_id": None}

    # role + group_id from group_members
    try:
        gm = (
            supabase_admin.table("group_members")
            .select("intern_role, group_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if gm.data:
            ctx["role"]     = gm.data[0].get("intern_role") or "frontend"
            ctx["group_id"] = gm.data[0].get("group_id")
    except Exception as e:
        logger.warning(f"_get_user_context group_members failed: {e}")

    # project_id from project_groups
    if ctx["group_id"]:
        try:
            pg = (
                supabase_admin.table("project_groups")
                .select("project_id")
                .eq("id", ctx["group_id"])
                .limit(1)
                .execute()
            )
            if pg.data:
                ctx["project_id"] = pg.data[0].get("project_id")
        except Exception as e:
            logger.warning(f"_get_user_context project_groups failed: {e}")

    # fallback: profiles.project_id + intern_role
    if not ctx["project_id"]:
        try:
            profile = (
                supabase_admin.table("profiles")
                .select("project_id, intern_role")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
            if profile.data:
                ctx["project_id"] = ctx["project_id"] or profile.data[0].get("project_id")
                if ctx["role"] == "frontend":
                    ctx["role"] = profile.data[0].get("intern_role") or "frontend"
        except Exception as e:
            logger.warning(f"_get_user_context profiles fallback failed: {e}")
    # ── Active sprint: 4-tier resolution ────────────────────────────────────────
    #
    # Root cause of the cross-group bleed: multiple sprints can share the same
    # group_id (e.g. "Sprint 0 — Tester" and "Sprint 0 — Backend" both have
    # group_id = bbbbbbbb-...). Filtering by group_id alone is therefore
    # ambiguous and returns whichever active sprint the DB finds first.
    #
    # The only UNAMBIGUOUS source is: an existing task already assigned to this
    # user. That task's sprint_id IS the user's sprint — no guessing needed.

    # Priority 1 (most precise): read sprint_id from the user's own tasks.
    # A task assigned to this user in this project tells us exactly which sprint
    # they belong to — even when group_id is shared across multiple sprints.
    try:
        q = (
            supabase_admin.table("tasks")
            .select("sprint_id")
            .eq("assigned_to", user_id)
            .not_.is_("sprint_id", "null")
            .order("created_at", desc=True)
            .limit(1)
        )
        if ctx["project_id"]:
            q = q.eq("project_id", ctx["project_id"])
        task_res = q.execute()
        if task_res.data:
            ctx["sprint_id"] = task_res.data[0]["sprint_id"]
            logger.info(
                f"_get_user_context P1: sprint from user task "
                f"sprint={ctx['sprint_id']} user={user_id}"
            )
    except Exception as e:
        logger.warning(f"_get_user_context task-based sprint lookup failed: {e}")

    # Priority 2: active sprint where intern_role matches — cross-references the
    # sprints table with the user's role so we pick the right sprint even when
    # group_id is shared. Sprint title conventionally contains the role name
    # (e.g. "Sprint 0 — Tester"), so filter by intern_role in the title.
    if not ctx["sprint_id"] and ctx["role"] and ctx["project_id"]:
        try:
            sprint = (
                supabase_admin.table("sprints")
                .select("id, title")
                .eq("project_id", ctx["project_id"])
                .eq("is_active", True)
                .ilike("title", f"%{ctx['role']}%")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if sprint.data:
                ctx["sprint_id"] = sprint.data[0]["id"]
                logger.info(
                    f"_get_user_context P2: sprint matched by role in title "
                    f"sprint={ctx['sprint_id']} title={sprint.data[0]['title']!r} role={ctx['role']}"
                )
        except Exception as e:
            logger.warning(f"_get_user_context role-title sprint lookup failed: {e}")

    # Priority 3: shared helper — active sprint by group_id / project_id.
    # Still useful when group_id is unique per sprint (most projects).
    if not ctx["sprint_id"] and (ctx["group_id"] or ctx["project_id"]):
        try:
            ctx["sprint_id"] = _resolve_active_sprint_for_user(
                group_id=ctx["group_id"],
                project_id=ctx["project_id"],
            )
            if ctx["sprint_id"]:
                logger.info(
                    f"_get_user_context P3: sprint from _resolve_active_sprint_for_user "
                    f"sprint={ctx['sprint_id']}"
                )
        except Exception as e:
            logger.warning(f"_get_user_context _resolve_active_sprint_for_user failed: {e}")

    # Priority 4 (last resort): project-wide sprint only (group_id IS NULL).
    # Never pick a sprint that belongs to another group.
    if not ctx["sprint_id"] and ctx["project_id"]:
        try:
            sprint = (
                supabase_admin.table("sprints")
                .select("id")
                .eq("project_id", ctx["project_id"])
                .eq("is_active", True)
                .is_("group_id", "null")
                .limit(1)
                .execute()
            )
            if sprint.data:
                ctx["sprint_id"] = sprint.data[0]["id"]
                logger.info(
                    f"_get_user_context P4: project-wide sprint "
                    f"sprint={ctx['sprint_id']}"
                )
        except Exception as e:
            logger.warning(f"_get_user_context project-wide sprint fallback failed: {e}")

    if not ctx["sprint_id"]:
        logger.warning(
            f"_get_user_context: no sprint found for user={user_id} "
            f"group={ctx['group_id']} project={ctx['project_id']} role={ctx['role']}. "
            "Task will be created without a sprint_id."
        )

    return ctx


def _pick_scenario(role: str) -> dict:
    bucket = _SCENARIOS.get(_resolve_role(role), _SCENARIOS["frontend"])
    return random.choice(bucket)


def _priority_label(p: str) -> str:
    return {"critical": "🔴 CRITICAL", "high": "🟠 HIGH", "medium": "🟡 MEDIUM", "low": "🟢 LOW"}.get(p, p.upper())


def _build_notif_body(scenario: dict) -> str:
    """Compact 2–3 line body for the notification bell."""
    avoid_short = scenario["avoid_breaking"][0] if scenario["avoid_breaking"] else "existing flows"
    return (
        f"{_priority_label(scenario['priority'])} | ⏰ {scenario['expected_time']}\n"
        f"Wants: {scenario['client_wants'][:120]}…\n"
        f"⚠️ Don't break: {avoid_short}"
    )


def _get_teammates(group_id: str, intern_role: str, exclude_user_id: str) -> list[str]:
    """
    Returns user_ids of teammates:
    same group_id AND same intern_role, excluding the triggering user.
    """
    try:
        res = (
            supabase_admin.table("group_members")
            .select("user_id")
            .eq("group_id", group_id)
            .eq("intern_role", intern_role)
            .neq("user_id", exclude_user_id)
            .execute()
        )
        return [r["user_id"] for r in (res.data or [])]
    except Exception as e:
        logger.warning(f"_get_teammates failed: {e}")
        return []


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class TriggerBody(BaseModel):
    scenario_id: Optional[str] = None   # pin a specific scenario (optional)
    sprint_id:   Optional[str] = None   # hard override — skip all resolution
    group_id:    Optional[str] = None   # group override
    intern_role: Optional[str] = None   # helps P2 role-title sprint lookup


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_client_requirement_change(
    body: TriggerBody,
    current_user: dict = Depends(get_current_user),
):
    """
    Immediately fire the client requirement change mode for the current user:
      1. Send a rich AI Mentor notification (with client wants, priority,
         expected time, and what NOT to break).
      2. Raise a ticket for the team (to_group_id = user's group →
         appears as *incoming* on the ticket board).
      3. Create a new task assigned to the user (purple in Kanban),
         placed in the user's own active sprint (group-scoped lookup first).
      4. Notify teammates (same group + same intern_role) with a brief alert.
    """
    user_id = current_user["id"]
    ctx     = _get_user_context(user_id)

    # body.intern_role from frontend (user?.intern_role) sharpens P2 sprint lookup
    # and prevents the profile query returning a stale/wrong role.
    role       = body.intern_role or ctx["role"]
    project_id = ctx["project_id"]
    # Frontend may send group_id / sprint_id explicitly — always trust those
    # over the backend's own resolution to prevent cross-group bleed.
    group_id   = body.group_id  or ctx["group_id"]
    sprint_id  = body.sprint_id or ctx["sprint_id"]

    logger.info(
        f"[CCR] trigger user={user_id} role={role} "
        f"group={group_id} sprint={sprint_id} "
        f"(body: role={body.intern_role} group={body.group_id} sprint={body.sprint_id})"
    )

    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="No active project found. Please join a project first.",
        )

    # ── Pick scenario based on user's intern_role ──────────────────────────
    resolved_role = _resolve_role(role)
    bucket = _SCENARIOS.get(resolved_role, _SCENARIOS["frontend"])
    if body.scenario_id:
        matched = [s for s in bucket if s["id"] == body.scenario_id]
        scenario = matched[0] if matched else random.choice(bucket)
    else:
        scenario = random.choice(bucket)

    due_date = (
        datetime.now(timezone.utc) + timedelta(days=scenario["expected_time_days"])
    ).isoformat()

    # ── 1. Create task → user's active sprint ──────────────────────────────
    # sprint_id is resolved via group_id first (see _get_user_context), so the
    # task will never land on a generic "sprint 0" belonging to another group.
    task_payload = {
        "title":                     f"[CCR] {scenario['title']}",
        "description":               scenario["task_description"],
        "assigned_to":               user_id,
        "intern_role":               role if role in (
                                         "frontend", "backend", "fullstack",
                                         "devops", "design", "tester", "ui_ux"
                                     ) else "fullstack",
        "status":                    "todo",
        "priority":                  scenario["priority"] if scenario["priority"] != "critical" else "high",
        "difficulty":                "hard",
        "due_date":                  due_date,
        "mid_sprint_changed":        True,
        "mid_sprint_change_reason":  "client_requirement_change",
        "mid_sprint_changed_at":     _now_iso(),
        "project_id":                project_id,
    }
    if sprint_id:
        task_payload["sprint_id"] = sprint_id
    if group_id:
        task_payload["group_id"] = group_id

    created_task = None
    try:
        task_res = supabase_admin.table("tasks").insert(task_payload).execute()
        created_task = task_res.data[0] if task_res.data else None
        logger.info(f"[CCR] Task created: {created_task and created_task.get('id')} "
                    f"sprint={sprint_id} user={user_id}")
    except Exception as e:
        logger.error(f"[CCR] Task creation failed: {e}", exc_info=True)

    # ── 2. Create ticket → to_group_id = user's own group ─────────────────
    # Setting to_group_id to the user's group makes this ticket appear as
    # an *incoming* ticket on their team's ticket board.
    created_ticket = None
    try:
        ticket_payload = {
            "title":        scenario["ticket_title"],
            "description":  scenario["ticket_description"],
            "type":         "feature_request",
            "priority":     scenario["priority"] if scenario["priority"] != "critical" else "high",
            "status":       "open",
            "project_id":   project_id,
            "created_by":   user_id,
        }
        if group_id:
            # from_group_id = originator, to_group_id = recipient team (same group
            # here because the CCR is addressed to the intern's own team)
            ticket_payload["from_group_id"] = group_id
            ticket_payload["to_group_id"]   = group_id

        ticket_res = supabase_admin.table("tickets").insert(ticket_payload).execute()
        created_ticket = ticket_res.data[0] if ticket_res.data else None
        logger.info(f"[CCR] Ticket created: {created_ticket and created_ticket.get('id')}")
    except Exception as e:
        logger.error(f"[CCR] Ticket creation failed: {e}", exc_info=True)

    ticket_id = created_ticket["id"] if created_ticket else None

    # ── 3. Rich AI Mentor notification → triggering user ──────────────────
    avoid_lines = "\n".join(f"  • {a}" for a in scenario["avoid_breaking"])
    rich_body = (
        f"What the client wants:\n{scenario['client_wants']}\n\n"
        f"Priority: {_priority_label(scenario['priority'])}\n"
        f"Expected time: {scenario['expected_time']}\n\n"
        f"Do NOT break:\n{avoid_lines}"
    )

    upsert_notification(
        user_id=user_id,
        key=f"ccr_{scenario['id']}",
        type_="client_change",
        title=f"🤖 AI Mentor: {scenario['title']}",
        body=_build_notif_body(scenario),
        icon="🤖",
        href="/internship/tasks",
    )

    # ── 4. Alert teammates: same group_id AND same intern_role ────────────
    if group_id:
        teammates = _get_teammates(group_id, role, user_id)
        for member_id in teammates:
            try:
                upsert_notification(
                    user_id=member_id,
                    key=f"ccr_team_{scenario['id']}",
                    type_="client_change",
                    title=f"📋 New Client Requirement: {scenario['title']}",
                    body=(
                        f"A new client change request has been raised. "
                        f"Priority: {_priority_label(scenario['priority'])}. "
                        f"Deadline: {scenario['expected_time']}. Check the ticket board."
                    ),
                    icon="📋",
                    href=f"/dashboard/ticket/{ticket_id}" if ticket_id else "/dashboard/ticket",
                )
            except Exception as e:
                logger.warning(f"[CCR] Teammate notification failed for member={member_id}: {e}")

    return {
        "ok":        True,
        "scenario":  scenario["id"],
        "title":     scenario["title"],
        "task_id":   created_task["id"] if created_task else None,
        "ticket_id": ticket_id,
        "role":      role,
        "sprint_id": sprint_id,
        "message":   (
            f"Client Requirement Change triggered for role '{role}'. "
            f"Task '{scenario['title']}' assigned to you and ticket raised for the team."
        ),
    }


@router.get("/scenarios")
async def list_scenarios(current_user: dict = Depends(get_current_user)):
    """
    Returns available scenarios for the current user's role.
    Useful for the frontend sim-mode selector.
    """
    user_id = current_user["id"]
    ctx     = _get_user_context(user_id)
    role    = ctx["role"]
    bucket  = _SCENARIOS.get(_resolve_role(role), _SCENARIOS["frontend"])
    return {
        "role":      role,
        "scenarios": [
            {
                "id":            s["id"],
                "title":         s["title"],
                "priority":      s["priority"],
                "expected_time": s["expected_time"],
                "client_wants":  s["client_wants"][:200],
            }
            for s in bucket
        ],
    }