'use client'

/**
 * useQABugFlood.js
 * Place at: src/lib/store/useQABugFlood.js
 *
 * Silently inserts ShopSphere bug tickets every time qa_bug_flood activates.
 *
 * Activation 1 → batch 1 (tickets 1-6)
 * Activation 2 → batch 2 (tickets 7-12)
 * Activation 3 → batch 1 again  (cycles)
 *
 * Reload-safe: sessionStorage prevents re-insertion if the mode is still
 * active when the user reloads the page mid-session.
 *
 * On deactivation → resolves all tickets inserted this session, reverts tasks.
 *
 * FIX: accepts `activeMode` as a parameter instead of calling useSimMode()
 * internally — this breaks the circular import with simModeStore.jsx.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import api from '@/lib/api'

// ── 12 ShopSphere bug templates ───────────────────────────────────────────────

const SHOPSPHERE_BUGS = [
  // ── BATCH 1 (index 0-5) ───────────────────────────────────────────────────
  {
    title: '[QA] 🛒 Cart total goes negative with stacked discount codes',
    description:
      'Regression found on checkout flow: applying two discount codes (e.g. SAVE10 + SUMMER20) drops the cart total below $0. The "Place Order" CTA becomes unresponsive and the session hangs.\n\nSteps to reproduce:\n1. Add 3+ items to cart (subtotal > $50)\n2. Apply code SAVE10\n3. Apply code SUMMER20\n4. Observe: total = -$4.00, CTA disabled\n\nExpected: second code should be rejected with an error toast once the total would go negative.\nSeverity: P1 — blocks ALL checkout paths. Affects 100% of users attempting multi-code checkout.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 🖼️ Product images broken on Safari 17 — CORS policy error',
    description:
      'All product catalogue images return a CORS policy error on Safari 17 (macOS Sonoma + iOS 17). The Next.js Image component renders broken-image placeholders across the entire product listing page and PDPs. Chrome and Firefox are unaffected.\n\nConsole error: "Cross-Origin Request Blocked: The Same Origin Policy disallows reading the remote resource at https://[supabase-url]/storage/..."\n\nLikely cause: Supabase Storage bucket CORS policy missing the Safari-compatible Access-Control-Allow-Origin header.\nImpact: ~20% of users (Safari market share). Affects /products, /products/[id], cart thumbnails.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 📦 Order status stuck — Realtime WebSocket drops after 30s',
    description:
      'The order management page (/orders) does not reflect status changes in real time. Supabase Realtime subscription disconnects after ~30 seconds of inactivity, and the UI never re-subscribes. Users see stale "Pending" status even after orders ship.\n\nConsole: "WebSocket connection closed unexpectedly (code 1006)"\n\nReproduction:\n1. Open /orders and observe a live order\n2. Trigger a backend status update (pending → shipped)\n3. Wait 35+ seconds — UI does not update\n4. Refresh page — correct status appears\n\nCritical for admin dashboard order tracking.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 💰 Admin revenue widget double-counts partially refunded orders',
    description:
      'The revenue aggregation on the admin analytics dashboard double-counts orders that have been partially refunded. A $120 order with a $20 refund is counted as $240 total revenue instead of $100.\n\nRoot cause: FastAPI aggregation query appears to CROSS JOIN the orders and refunds tables without a proper GROUP BY, inflating revenue for any order with a refund record.\n\nAffects: all date-range filters (today / week / month / all-time). Finance team flagged this as a data integrity blocker for their monthly reporting.\nExpected: revenue = original_amount - total_refunded.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 📱 Checkout form unusable on iPhone SE — fields overlap keyboard',
    description:
      'On viewports ≤ 375px (iPhone SE 1st + 2nd gen), the shipping address form fields in the checkout flow overlap each other. The city/state/zip row collapses into a single unreadable line, and the "Continue to Payment" CTA is hidden behind the iOS keyboard.\n\nTailwind responsive classes misconfigured — `sm:grid-cols-2` breakpoint not applying correctly at 375px.\n\nTested on:\n- iPhone SE (real device, iOS 17)\n- Chrome DevTools 375px viewport\n- Pixel 4a (fine)\n\nBlocks mobile checkout for ~8% of users.',
    type: 'bug_report',
    priority: 'medium',
  },
  {
    title: '[QA] 🔍 Search autocomplete shows deleted products for ~10 minutes',
    description:
      'After an admin deletes a product from the catalogue, the search autocomplete still suggests the deleted product for 8-12 minutes. Clicking a stale suggestion navigates to a 404 page with no recovery path.\n\nRoot cause: FastAPI search endpoint uses an in-memory cached product index that is only refreshed on a TTL interval, not on product deletion events.\n\nExpected: deleted products should be excluded from all search results immediately upon deletion (synchronous cache invalidation on DELETE /api/products/{id}).\nUX impact: confusing 404 dead-ends erode user trust in the catalogue.',
    type: 'bug_report',
    priority: 'medium',
  },

  // ── BATCH 2 (index 6-11) ──────────────────────────────────────────────────
  {
    title: '[QA] 🔐 Password reset link does not expire after first use',
    description:
      'Security regression: the one-time password reset link generated by Supabase Auth remains valid indefinitely after it has already been used. A second visit to the same reset URL allows setting a new password without re-authentication.\n\nReproduction:\n1. Request a password reset\n2. Open the link, set a new password\n3. Re-open the same link — you can set ANOTHER new password\n\nSeverity: P0 — account takeover vector if the reset email is intercepted.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 🧾 PDF invoice generation crashes for orders > 20 line items',
    description:
      'The FastAPI /api/orders/{id}/invoice endpoint returns HTTP 500 for any order containing more than 20 distinct line items. The server log shows an unhandled IndexError in the reportlab layout loop when the items overflow onto a second PDF page.\n\nError: `IndexError: list index out of range` in `invoice_generator.py:142`\n\nWorkaround: none — admin cannot manually generate invoices for large orders.\nImpact: finance team blocked from issuing invoices for ~15% of revenue by value.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 🏷️ Coupon "free shipping" flag silently ignored at checkout',
    description:
      'Coupons created with the `free_shipping: true` flag in the admin panel do not remove the shipping fee at checkout. The coupon is accepted and the discount amount is applied correctly, but the shipping line item ($8.99–$15.99) remains on the order total.\n\nRoot cause: the FastAPI checkout service reads the coupon discount_amount but never reads the free_shipping field before computing the order summary.\n\nExpected: if the applied coupon has free_shipping = true, set shipping_cost = 0 in the order.\nImpact: customers charged for shipping they were promised free — high chargeback and complaint risk.',
    type: 'bug_report',
    priority: 'high',
  },
  {
    title: '[QA] 📊 "Top Products" widget ignores dashboard date filter',
    description:
      'On the admin analytics dashboard, changing the date-range filter (e.g. "Last 30 Days", "This Month", "Custom Range") correctly updates the Revenue and Orders widgets, but the "Top Products by Sales" widget always continues to display data for the last 7 days.\n\nRoot cause: the Top Products component passes a hardcoded `range=7d` query param to the FastAPI endpoint instead of reading from the shared dashboard filter context.\n\nAffects: all admin users. Misleads merchandising decisions when reviewing monthly or seasonal performance.',
    type: 'bug_report',
    priority: 'medium',
  },
  {
    title: '[QA] 🔄 "Continue Shopping" on order confirmation loops to empty cart',
    description:
      'After a successful order is placed, the order confirmation page shows a "Continue Shopping" button. Clicking it navigates the user to /cart instead of /products, and because the cart was just cleared the user lands on an empty cart page.\n\nExpected: "Continue Shopping" should navigate to /products (or the last-visited category page).\n\nAdditional: the browser back button from the confirmation page re-submits the order, creating duplicate orders.\nUX impact: breaks the post-purchase re-engagement loop.',
    type: 'bug_report',
    priority: 'medium',
  },
  {
    title: '[QA] 🌐 Currency symbol hardcoded as "$" across all locale builds',
    description:
      'ShopSphere has locale config for en-GB, en-AU, and en-CA, but all price displays across the product catalogue, cart, checkout, and order history render the USD "$" symbol regardless of the selected locale.\n\nRoot cause: the `formatPrice()` utility in `src/lib/utils/currency.js` hardcodes `symbol: "$"` instead of reading from the locale-aware `Intl.NumberFormat` config.\n\nExpected: en-GB → "£", en-AU → "A$", en-CA → "CA$".\nBlocks international market launch planned for next sprint.',
    type: 'bug_report',
    priority: 'medium',
  },
]

const BATCH_SIZE = 6
const TOTAL_BATCHES = Math.ceil(SHOPSPHERE_BUGS.length / BATCH_SIZE)

// ── Session-storage keys ──────────────────────────────────────────────────────
const SS_KEY_RAN    = 'qabf_session_ran'
const SS_KEY_CURSOR = 'qabf_batch_cursor'

function getStoredCursor() {
  try {
    const v = sessionStorage.getItem(SS_KEY_CURSOR)
    return v !== null ? parseInt(v, 10) : 0
  } catch { return 0 }
}

function setStoredCursor(val) {
  try { sessionStorage.setItem(SS_KEY_CURSOR, String(val)) } catch { /* non-fatal */ }
}

function markSessionRan() {
  try { sessionStorage.setItem(SS_KEY_RAN, 'true') } catch { /* non-fatal */ }
}

function clearSessionFlags() {
  try {
    sessionStorage.removeItem(SS_KEY_RAN)
    sessionStorage.removeItem(SS_KEY_CURSOR)
  } catch { /* non-fatal */ }
}

function didAlreadyRunThisSession() {
  try { return sessionStorage.getItem(SS_KEY_RAN) === 'true' } catch { return false }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

// FIX: accepts activeMode as a parameter instead of reading it from useSimMode()
// This avoids a circular import: simModeStore → useQABugFlood → simModeStore
export function useQABugFlood(activeMode) {
  const [phase, setPhase]                       = useState('idle')
  const [generatedTickets, setGeneratedTickets] = useState([])
  const [reopenedTasks, setReopenedTasks]       = useState([])
  const [error, setError]                       = useState(null)

  const hasLaunched = useRef(false)

  // ── Detect mode activation / deactivation ─────────────────────────────────

  useEffect(() => {
    if (activeMode !== 'qa_bug_flood') {
      // Mode turned off — reset state so next activation is fresh
      hasLaunched.current = false
      clearSessionFlags()
      setPhase('idle')
      setGeneratedTickets([])
      setReopenedTasks([])
      setError(null)
      return
    }

    // Guard: already launched in this render cycle
    if (hasLaunched.current) return

    // Guard: page reloaded while mode still active — tickets already in DB
    if (didAlreadyRunThisSession()) {
      hasLaunched.current = true
      setPhase('done')
      return
    }

    hasLaunched.current = true

    const batchToRun = getStoredCursor() % TOTAL_BATCHES
    setStoredCursor(batchToRun + 1)
    markSessionRan()

    runBatch(batchToRun)
  }, [activeMode])

  // ── Insert one batch of 6 tickets ─────────────────────────────────────────

  async function runBatch(batch) {
    setPhase('inserting')
    setError(null)

    try {
      // 1. User / project context
      const { data: me } = await api.get('/api/auth/me')
      if (!me?.project_id) {
        setError('Not assigned to a project.')
        setPhase('error')
        return
      }

      // 2. Resolve group (best-effort)
      let myGroup = null
      try {
        const { data: allGroups } = await api.get(`/api/projects/${me.project_id}/groups`)
        const groups = allGroups ?? []
        if (me.group_id) myGroup = groups.find(g => g.id === me.group_id) ?? null
        if (!myGroup) {
          const { data: teamData } = await api.get(`/api/projects/${me.project_id}/team`)
          const mine = (teamData?.team ?? []).find(m => m.user_id === me.id)
          if (mine?.group_id) {
            myGroup = groups.find(g => g.id === mine.group_id)
              ?? { id: mine.group_id, name: mine.group_name ?? 'My Team' }
          }
        }
      } catch { /* non-fatal */ }

      // 3. Active sprint (best-effort)
      let activeSprint = null
      try {
        const { data: sprints } = await api.get(`/api/projects/${me.project_id}/sprints`)
        activeSprint = (sprints ?? []).find(s => s.is_active) ?? null
      } catch { /* non-fatal */ }

      // 4. Pick bug slice
      const start = batch * BATCH_SIZE
      const slice = SHOPSPHERE_BUGS.slice(start, start + BATCH_SIZE)

      // 5. Insert tickets with stagger
      const inserted = []
      for (let i = 0; i < slice.length; i++) {
        const bug = slice[i]
        try {
          const { data: ticket } = await api.post('/api/tickets', {
            title:         bug.title,
            description:   bug.description,
            type:          bug.type,
            priority:      bug.priority,
            status:        'open',
            project_id:    me.project_id,
            from_group_id: myGroup?.id ?? null,
            to_group_id:   myGroup?.id ?? null,
          })
          if (ticket) inserted.push(ticket)
        } catch (err) {
          console.warn(`[QABugFlood] insert failed (batch ${batch}, idx ${i}):`, err?.response?.data ?? err.message)
          inserted.push({
            id:         `sim-local-${batch}-${i}`,
            title:      bug.title,
            priority:   bug.priority,
            type:       bug.type,
            status:     'open',
            created_at: new Date().toISOString(),
            _local:     true,
          })
        }

        // Update count incrementally so the panel shows live progress
        setGeneratedTickets(prev => [...prev, inserted[inserted.length - 1]])
        await delay(250)
      }

      // 6. Reopen up to 4 "done" sprint tasks (best-effort)
      const tasksReopened = []
      if (activeSprint?.id) {
        try {
          const { data: sprintTasks } = await api.get(`/api/sprints/${activeSprint.id}/tasks`)
          const done = (sprintTasks ?? []).filter(t => t.status === 'done').slice(0, 4)
          for (const task of done) {
            try {
              await api.patch(`/api/tasks/${task.id}`, {
                status:                   'review',
                previous_status:          'done',
                mid_sprint_changed:       true,
                mid_sprint_change_reason: '[QA Bug Flood] Task reopened — related bugs reported by QA.',
                mid_sprint_changed_at:    new Date().toISOString(),
              })
              tasksReopened.push(task)
            } catch { /* non-fatal */ }
          }
        } catch { /* non-fatal */ }
      }
      setReopenedTasks(tasksReopened)

      // 7. Bump sprint risk summary (best-effort)
      if (myGroup?.id) {
        try {
          await api.post('/api/standup-summaries', {
            group_id:         myGroup.id,
            date:             new Date().toISOString().split('T')[0],
            summary_text:     `🚨 QA Bug Flood (batch ${batch + 1}): ${inserted.length} critical bugs filed against ShopSphere. ${tasksReopened.length} tasks reopened. Sprint at risk.`,
            sprint_risk:      'critical',
            blocker_count:    inserted.filter(t => t.priority === 'high').length,
            submission_count: 0,
            late_count:       0,
            missed_count:     0,
          })
        } catch { /* non-fatal */ }
      }

      setPhase('done')

    } catch (err) {
      console.error('[QABugFlood] fatal:', err)
      setError(err?.response?.data?.detail ?? err.message ?? 'Unknown error')
      setPhase('error')
    }
  }

  // ── Reset: resolve tickets & revert tasks ─────────────────────────────────

  const resetFlood = useCallback(async () => {
    for (const ticket of generatedTickets.filter(t => !t._local)) {
      try {
        await api.patch(`/api/tickets/${ticket.id}`, {
          status:          'resolved',
          resolution_note: '[Sim reset] QA Bug Flood ended — auto-resolved.',
        })
      } catch { /* non-fatal */ }
    }
    for (const task of reopenedTasks) {
      try {
        await api.patch(`/api/tasks/${task.id}`, {
          status:                   'done',
          mid_sprint_changed:       false,
          mid_sprint_change_reason: null,
          previous_status:          null,
        })
      } catch { /* non-fatal */ }
    }
    // Clear state immediately so the panel empties before it unmounts
    setGeneratedTickets([])
    setReopenedTasks([])
    setPhase('idle')
    clearSessionFlags()
  }, [generatedTickets, reopenedTasks])

  return { phase, generatedTickets, reopenedTasks, error, resetFlood }
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}