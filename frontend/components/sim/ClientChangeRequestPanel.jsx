'use client'

/**
 * ClientChangeRequestPanel.jsx
 * ─────────────────────────────
 * When CCR mode activates:
 *   1. Toast notification from AI Mentor appears with sound
 *   2. Backend is called automatically → task + ticket + notifications created
 *   3. Info panel slides in showing: what client wants, priority, expected time,
 *      what NOT to break
 *   4. Task appears in Kanban with purple highlight (handled by TaskCard)
 *   5. Notification appears in the bell (handled by backend)
 *
 * ROLE-BASED SCENARIOS
 *   Scenario is chosen based on the user's intern_role (read from auth store):
 *     frontend → Quick-View Modal / Checkout Stepper
 *     backend  → Search & Filter API / Order Webhook
 *     ui_ux    → Mobile Bottom Nav / Empty States
 *     tester   → E2E Checkout Tests / API Contract Tests
 *   Falls back to the first frontend scenario for unknown roles.
 */

import { useState, useEffect, useRef } from 'react'
import { useSimMode } from '@/lib/store/simModeStore'
import { useAuthStore } from '@/lib/store/authStore'

// ── Sound ─────────────────────────────────────────────────────────────────────
function playNotifSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const now = ctx.currentTime
    const osc1 = ctx.createOscillator()
    const osc2 = ctx.createOscillator()
    const gain = ctx.createGain()
    osc1.type = 'sine'; osc2.type = 'sine'
    osc1.frequency.setValueAtTime(880, now)
    osc1.frequency.exponentialRampToValueAtTime(1100, now + 0.1)
    osc2.frequency.setValueAtTime(660, now + 0.12)
    osc2.frequency.exponentialRampToValueAtTime(880, now + 0.22)
    gain.gain.setValueAtTime(0.18, now)
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35)
    osc1.connect(gain); osc2.connect(gain); gain.connect(ctx.destination)
    osc1.start(now); osc1.stop(now + 0.12)
    osc2.start(now + 0.12); osc2.stop(now + 0.35)
  } catch {}
}

// ── Role-specific CCR Scenarios ───────────────────────────────────────────────
// Each role has 2 scenarios; one is picked randomly when CCR activates.
// backendScenarioId must match the id in client_requirement_change.py

const CCR_SCENARIOS_BY_ROLE = {
  frontend: [
    {
      id: 'quick_view_modal',
      crNumber: 'CR-201',
      client: 'E-Commerce Client',
      title: 'Add Product Quick-View Modal',
      summary: 'Client wants customers to preview product images, price, and variants in a modal on the catalogue — without navigating away — to reduce bounce rate.',
      priority: 'high',
      effort: 8,
      sprintImpact: '+2 days',
      deadline: '48 hours',
      clientWants: 'Implement a Quick-View modal on the product catalogue. When a user hovers or clicks "Quick View" on any product card, a modal appears showing: product images (with thumbnail strip), title, price, variant selector (size/colour), stock count, and an "Add to Cart" button.',
      expectedTime: '2 working days',
      avoidBreaking: [
        'Product listing page performance (LCP must stay < 2.5s)',
        'Existing click-to-product-detail navigation (modal is additive)',
        'Cart state and add-to-cart flow',
        'Mobile responsiveness of the product grid',
      ],
      backendScenarioId: 'ccr_fe_001',
    },
    {
      id: 'checkout_stepper',
      crNumber: 'CR-202',
      client: 'E-Commerce Client',
      title: 'Checkout Multi-Step Progress Stepper',
      summary: 'Checkout drop-off is high. Client wants a visual 4-step progress indicator (Cart → Address → Payment → Confirmation) so users always know where they are.',
      priority: 'medium',
      effort: 6,
      sprintImpact: '+3 days',
      deadline: '72 hours',
      clientWants: 'Redesign the checkout page with a clear 4-step stepper: Cart Review → Shipping Address → Payment → Order Confirmation. Each step must validate before allowing progression. Users must be able to go back to previous steps without losing their data.',
      expectedTime: '3 working days',
      avoidBreaking: [
        'Form validation and field state across steps',
        'Payment gateway callbacks (Razorpay/Stripe)',
        'Order summary sidebar on every step',
        'Browser back-button behaviour',
      ],
      backendScenarioId: 'ccr_fe_002',
    },
  ],

  backend: [
    {
      id: 'server_side_search',
      crNumber: 'CR-301',
      client: 'E-Commerce Client',
      title: 'Products API — Server-Side Search & Filters',
      summary: 'The catalogue is slow with 500+ products. Client needs server-side search with text, category, price range, ratings, and in-stock filters.',
      priority: 'high',
      effort: 8,
      sprintImpact: '+2 days',
      deadline: '48 hours',
      clientWants: 'Add server-side search and filtering to GET /api/products. Supported filters: ?search=, ?category=, ?min_price=, ?max_price=, ?min_rating=, ?in_stock=true. Results must be sortable by price_asc, price_desc, rating, newest.',
      expectedTime: '2 working days',
      avoidBreaking: [
        'Existing GET /api/products response shape — filters are additive params',
        'Pagination (page, limit) must work alongside new filters',
        'Admin dashboard product management APIs',
        'Cart and order references to product IDs',
      ],
      backendScenarioId: 'ccr_be_001',
    },
    {
      id: 'order_webhook',
      crNumber: 'CR-302',
      client: 'E-Commerce Client',
      title: 'Order Status Webhook & Email Notification',
      summary: "Every order status change must trigger an HTML email to the customer AND a webhook POST to the logistics partner's endpoint.",
      priority: 'high',
      effort: 10,
      sprintImpact: '+3 days',
      deadline: '5 days',
      clientWants: 'On every order status change (confirmed → shipped → delivered / cancelled): (1) send an HTML email to the customer using a branded template, and (2) POST a webhook to the logistics partner URL with the full order JSON payload.',
      expectedTime: '3 working days',
      avoidBreaking: [
        'PATCH /api/orders/{id}/status response contract',
        'Admin order management dashboard',
        'Existing GET /api/orders history endpoints',
        'Payment refund flow on cancellation',
      ],
      backendScenarioId: 'ccr_be_002',
    },
  ],

  ui_ux: [
    {
      id: 'mobile_bottom_nav',
      crNumber: 'CR-401',
      client: 'E-Commerce Client',
      title: 'Mobile Bottom Navigation Bar',
      summary: 'Mobile UX review found users struggle to find categories and cart. Client needs a sticky bottom nav bar on mobile: Home, Categories, Search, Cart (with badge), Profile.',
      priority: 'high',
      effort: 6,
      sprintImpact: '+2 days',
      deadline: '48 hours',
      clientWants: 'Add a sticky bottom navigation bar for mobile (≤768px) with 5 tabs: Home, Categories, Search (opens full-screen overlay), Cart (live item count badge), Profile. Active tab highlighted with brand colour.',
      expectedTime: '2 working days',
      avoidBreaking: [
        'Desktop header navigation (bottom nav is mobile-only ≤768px)',
        'Cart item count badge real-time updates',
        'All deep-link routes must work from bottom nav',
        'iPhone safe-area padding for notch/home bar',
      ],
      backendScenarioId: 'ccr_ux_001',
    },
    {
      id: 'empty_states',
      crNumber: 'CR-402',
      client: 'E-Commerce Client',
      title: 'Redesign Empty States & Onboarding Tooltips',
      summary: 'New users drop off at blank pages. Each empty state (cart, orders, wishlist) needs an illustration, helpful copy, and a clear CTA. Add first-time onboarding tooltips.',
      priority: 'medium',
      effort: 7,
      sprintImpact: '+3 days',
      deadline: '72 hours',
      clientWants: 'Design and implement empty state screens for: Cart (empty), Order History, Wishlist, Search no-results. Each needs an SVG illustration, supportive copy, and a primary CTA. Add a one-time tooltip walkthrough for first-time users on home and product pages.',
      expectedTime: '3 working days',
      avoidBreaking: [
        'Existing page routing and layout structure',
        'Cart/order state logic — empty state is purely presentational',
        'Tooltips must be keyboard-dismissible (Escape)',
        'Dark-mode colours must use existing design tokens',
      ],
      backendScenarioId: 'ccr_ux_002',
    },
  ],

  tester: [
    {
      id: 'e2e_checkout',
      crNumber: 'CR-501',
      client: 'E-Commerce Client',
      title: 'E2E Test Coverage for Checkout Flow',
      summary: 'Before go-live, all checkout edge cases must be covered: happy path, failed payments, out-of-stock blocking, and coupon code validation.',
      priority: 'critical',
      effort: 8,
      sprintImpact: '+2 days',
      deadline: '48 hours',
      clientWants: 'Write comprehensive E2E tests (Playwright/Cypress) for the checkout flow: happy path, failed payment, out-of-stock block, coupon code validation, and address form edge cases. All tests must pass in CI against staging.',
      expectedTime: '2 working days',
      avoidBreaking: [
        'Existing unit test suite (no component logic changes)',
        'CI/CD pipeline test runner configuration',
        'Payment sandbox environment credentials',
        'DB seeding scripts — add fixtures, do not modify existing',
      ],
      backendScenarioId: 'ccr_te_001',
    },
    {
      id: 'api_contract_tests',
      crNumber: 'CR-502',
      client: 'E-Commerce Client',
      title: 'API Contract & Regression Test Suite',
      summary: 'A silent regression reached production. Client needs contract tests for all critical API endpoints so breakages are caught in CI before merge.',
      priority: 'high',
      effort: 9,
      sprintImpact: '+3 days',
      deadline: '72 hours',
      clientWants: 'Implement API contract tests for: POST /api/orders, GET /api/products, POST /api/cart/add, POST /api/auth/login. Tests must validate response schema, status codes, and required fields. Must run automatically on every PR and complete within 5 minutes.',
      expectedTime: '3 working days',
      avoidBreaking: [
        'Current CI pipeline — add contract tests as a new parallel step',
        'Staging environment DB — use isolated test fixtures only',
        'PR review workflow must not be blocked by slow tests (< 5 min)',
        'Existing Postman collections — migrate, do not delete',
      ],
      backendScenarioId: 'ccr_te_002',
    },
  ],
}

// Role aliases — map profile intern_role values → scenario bucket keys
const ROLE_BUCKET = {
  frontend:  'frontend',
  backend:   'backend',
  fullstack: 'backend',
  devops:    'backend',
  design:    'ui_ux',
  ui_ux:     'ui_ux',
  tester:    'tester',
}

/** Pick the right scenario bucket for the current user's role */
function getScenariosForRole(internRole) {
  const key = ROLE_BUCKET[internRole] || 'frontend'
  return CCR_SCENARIOS_BY_ROLE[key]
}

// ── Priority helpers ──────────────────────────────────────────────────────────
const PRIORITY_META = {
  critical: { label: '🔴 CRITICAL', color: '#ef4444' },
  high:     { label: '🟠 HIGH',     color: '#f97316' },
  medium:   { label: '🟡 MEDIUM',   color: '#f59e0b' },
  low:      { label: '🟢 LOW',      color: '#10b981' },
}

// ── Helper: read the custom JWT from localStorage (same as api.js) ─────────────
function getAuthToken() {
  try {
    if (typeof window === 'undefined') return null
    const stored = localStorage.getItem('internx-auth')
    if (!stored) return null
    const parsed = JSON.parse(stored)
    return parsed?.state?.token || null
  } catch {
    return null
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function CCRToast({ scenario, onOpen, visible }) {
  const [show, setShow] = useState(false)
  const [exiting, setExiting] = useState(false)

  useEffect(() => {
    if (visible) {
      const t = setTimeout(() => setShow(true), 200)
      return () => clearTimeout(t)
    } else {
      setShow(false)
    }
  }, [visible])

  const dismiss = () => { setExiting(true); setTimeout(onOpen, 350) }

  if (!show) return null

  const pm = PRIORITY_META[scenario.priority] || PRIORITY_META.medium

  return (
    <>
      <style>{`
        @keyframes ccr-toast-in  { from { transform: translateX(120%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        @keyframes ccr-toast-out { from { transform: translateX(0); opacity: 1; } to { transform: translateX(120%); opacity: 0; } }
        @keyframes ccr-ring { 0%,100%{transform:rotate(0)} 15%{transform:rotate(-12deg)} 30%{transform:rotate(12deg)} 45%{transform:rotate(-8deg)} 60%{transform:rotate(8deg)} 75%{transform:rotate(-4deg)} }
        @keyframes ccr-glow { 0%,100%{box-shadow:0 0 0 0 rgba(139,92,246,0.4)} 50%{box-shadow:0 0 0 8px rgba(139,92,246,0)} }
      `}</style>
      <div style={{
        position: 'fixed', bottom: 24, right: 24, zIndex: 9999, width: 360,
        animation: `${exiting ? 'ccr-toast-out' : 'ccr-toast-in'} 0.35s cubic-bezier(0.34,1.1,0.64,1) forwards`,
      }}>
        <div style={{
          background: '#fff', border: '1.5px solid #c4b5fd', borderRadius: 16,
          boxShadow: '0 20px 60px rgba(139,92,246,0.18), 0 4px 16px rgba(0,0,0,0.1)',
          overflow: 'hidden', position: 'relative',
        }}>
          <div style={{ height: 4, background: 'linear-gradient(90deg, #8b5cf6, #6d28d9, #a855f7)', animation: 'ccr-glow 2s ease-in-out infinite' }} />

          <div style={{ padding: '16px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
              <div style={{
                width: 42, height: 42, borderRadius: 12, flexShrink: 0,
                background: 'linear-gradient(135deg, #ede9fe, #faf5ff)',
                border: '1.5px solid #c4b5fd',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22,
                animation: 'ccr-ring 0.8s ease 0.5s',
              }}>🤖</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    AI Mentor Alert
                  </span>
                  <span style={{
                    fontSize: 9, fontWeight: 800, padding: '1px 6px', borderRadius: 99,
                    background: '#fef2f2', color: '#ef4444', border: '1px solid #fecaca',
                  }}>
                    {pm.label}
                  </span>
                </div>
                <p style={{ fontSize: 14, fontWeight: 700, color: '#111827', margin: 0, lineHeight: 1.3 }}>
                  {scenario.title}
                </p>
              </div>
            </div>

            <div style={{
              padding: '10px 12px', borderRadius: 8,
              background: '#faf5ff', border: '1px solid #ddd6fe',
              marginBottom: 14, fontSize: 12, color: '#5b21b6', lineHeight: 1.55,
            }}>
              <strong>📣 Client wants:</strong> {scenario.clientWants.slice(0, 160)}…
              <br /><br />
              <strong>⏰ Expected:</strong> {scenario.expectedTime}
              <br />
              <strong>⚠️ Don't break:</strong> {scenario.avoidBreaking[0]}
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={onOpen} style={{
                flex: 1, padding: '10px 16px',
                background: 'linear-gradient(135deg, #7c3aed, #6d28d9)',
                color: '#fff', fontSize: 13, fontWeight: 700,
                border: 'none', borderRadius: 10, cursor: 'pointer', fontFamily: 'inherit',
                boxShadow: '0 4px 12px rgba(109,40,217,0.35)',
              }}>
                View Full Brief →
              </button>
              <button onClick={dismiss} style={{
                padding: '10px 14px',
                background: '#f9fafb', color: '#6b7280',
                fontSize: 12, fontWeight: 600,
                border: '1.5px solid #e5e7eb', borderRadius: 10, cursor: 'pointer', fontFamily: 'inherit',
              }}>
                Dismiss
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function ClientChangeRequestPanel() {
  const { activeMode, deactivateMode } = useSimMode()
  const user = useAuthStore(s => s.user)
  // intern_role IS reliably on user — send it so the backend can do a
  // role-title sprint lookup (P2) when user task lookup (P1) returns nothing.
  // sprint_id / group_id are NOT stored in authStore so we don't send them;
  // the backend's P1 task-based lookup handles it from the DB.
  const internRole = user?.intern_role ?? null

  const [phase, setPhase] = useState('toast') // toast | info | done
  const [triggered, setTriggered] = useState(false)
  const [createdTaskId, setCreatedTaskId] = useState(null)
  const [createdTicketId, setCreatedTicketId] = useState(null)
  const [triggering, setTriggering] = useState(false)
  const [triggerError, setTriggerError] = useState(null)
  const [scenario, setScenario] = useState(null)
  const soundPlayed = useRef(false)

  const isActive = activeMode === 'client_change_request'

  // ── Resolve scenario from user's intern_role ──────────────────────────────
  useEffect(() => {
    if (isActive && !scenario) {
      const internRole = user?.intern_role || 'frontend'
      const bucket = getScenariosForRole(internRole)
      // Pick a random scenario from the role bucket
      setScenario(bucket[Math.floor(Math.random() * bucket.length)])
    }
  }, [isActive, user, scenario])

  // Play sound on activation & reset on deactivation
  useEffect(() => {
    if (isActive && !soundPlayed.current) {
      soundPlayed.current = true
      playNotifSound()
    }
    if (!isActive) {
      soundPlayed.current = false
      setPhase('toast')
      setTriggered(false)
      setCreatedTaskId(null)
      setCreatedTicketId(null)
      setTriggering(false)
      setTriggerError(null)
      setScenario(null)
    }
  }, [isActive])

  // Auto-trigger backend when toast is shown and scenario is ready
  useEffect(() => {
    if (!isActive || triggered || !scenario) return

    const triggerBackend = async () => {
      setTriggering(true)
      setTriggerError(null)
      try {
        const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
        const token = getAuthToken()

        if (!token) {
          console.warn('[CCR] No auth token found in localStorage — is the user logged in?')
          setTriggerError('Not authenticated. Please log in again.')
          setTriggered(true)
          setTriggering(false)
          return
        }

        const res = await fetch(`${API}/api/client-requirement-change/trigger`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify({
            scenario_id: scenario.backendScenarioId,
            // Send intern_role so the backend's P2 sprint lookup
            // (role-title match) works even before any task exists.
            ...(internRole && { intern_role: internRole }),
          }),
        })

        if (res.ok) {
          const json = await res.json()
          if (json.task_id)   setCreatedTaskId(json.task_id)
          if (json.ticket_id) setCreatedTicketId(json.ticket_id)

          // Notify Kanban to refresh immediately (no need to wait 30s poll)
          if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('ccr:task-created', {
              detail: { task_id: json.task_id, ticket_id: json.ticket_id }
            }))
          }
        } else {
          const errText = await res.text().catch(() => res.status)
          console.warn('[CCR] Backend returned non-OK:', res.status, errText)
          setTriggerError(`Server error ${res.status}. Check backend logs.`)
        }
      } catch (err) {
        console.warn('[CCR] Backend call failed:', err)
        setTriggerError('Could not reach backend. Is it running on port 8000?')
      } finally {
        setTriggered(true)
        setTriggering(false)
      }
    }

    triggerBackend()
  }, [isActive, triggered, scenario])

  if (!isActive || !scenario) return null

  const pm = PRIORITY_META[scenario.priority] || PRIORITY_META.medium

  return (
    <>
      <style>{`
        @keyframes ccr-info-in {
          from { transform: translateY(20px) scale(0.96); opacity: 0; }
          to   { transform: translateY(0) scale(1); opacity: 1; }
        }
        @keyframes ccr-dot {
          0%,100% { box-shadow: 0 0 0 0 rgba(139,92,246,0.5); }
          50%      { box-shadow: 0 0 0 6px rgba(139,92,246,0); }
        }
        @keyframes ccr-check-in {
          from { transform: scale(0); opacity: 0; }
          to   { transform: scale(1); opacity: 1; }
        }
        @keyframes ccr-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>

      {/* Phase 1: Toast notification */}
      {phase === 'toast' && (
        <CCRToast
          scenario={scenario}
          visible={isActive}
          onOpen={() => setPhase('info')}
        />
      )}

      {/* Phase 2: Full info panel */}
      {(phase === 'info' || phase === 'done') && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 9000,
          background: 'rgba(0,0,0,0.4)', backdropFilter: 'blur(6px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          padding: 16,
        }}>
          <div style={{
            width: '100%', maxWidth: 560, maxHeight: '88vh',
            background: '#fff', borderRadius: 20,
            boxShadow: '0 32px 96px rgba(0,0,0,0.22)',
            display: 'flex', flexDirection: 'column',
            animation: 'ccr-info-in 0.3s cubic-bezier(0.34,1.2,0.64,1)',
            overflow: 'hidden',
          }}>

            {/* ── Header ── */}
            <div style={{
              padding: '16px 20px 14px',
              background: 'linear-gradient(135deg, #faf5ff, #ede9fe)',
              borderBottom: '1.5px solid #c4b5fd', flexShrink: 0,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{
                  width: 8, height: 8, borderRadius: '50%', background: '#8b5cf6', flexShrink: 0,
                  animation: 'ccr-dot 1.4s ease-in-out infinite',
                }} />
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 10, fontWeight: 800, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                      {scenario.crNumber}
                    </span>
                    <span style={{
                      fontSize: 9, fontWeight: 800, padding: '1px 7px', borderRadius: 99,
                      background: '#ede9fe', color: '#7c3aed', border: '1px solid #c4b5fd',
                    }}>🤖 AI MENTOR</span>
                    <span style={{
                      fontSize: 9, fontWeight: 800, padding: '1px 7px', borderRadius: 99,
                      background: pm.color + '15', color: pm.color,
                      border: `1px solid ${pm.color}40`,
                    }}>{pm.label}</span>
                  </div>
                  <p style={{ fontSize: 16, fontWeight: 800, color: '#111827', margin: '2px 0 0', lineHeight: 1.2 }}>
                    {scenario.title}
                  </p>
                  <p style={{ fontSize: 11, color: '#6b7280', margin: '3px 0 0' }}>
                    From: {scenario.client} · Deadline: {scenario.deadline}
                  </p>
                </div>
                <button onClick={() => { deactivateMode() }} style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: 18, color: '#9ca3af', padding: 4, lineHeight: 1,
                }}>×</button>
              </div>
            </div>

            {/* ── Scrollable body ── */}
            <div style={{
              flex: 1, overflowY: 'auto', padding: 20,
              display: 'flex', flexDirection: 'column', gap: 16,
            }}>

              {/* Situation */}
              <div style={{
                padding: '10px 14px', borderRadius: 10,
                background: '#fffbeb', border: '1px solid #fde68a',
                fontSize: 12, color: '#78350f', lineHeight: 1.6,
              }}>
                <strong>📣 Situation:</strong> {scenario.summary}
              </div>

              {/* ── AI Mentor Brief Card ── */}
              <div style={{
                borderRadius: 14, overflow: 'hidden',
                border: '2px solid #e9d5ff',
                background: 'linear-gradient(135deg, #faf5ff 0%, #f5f3ff 100%)',
                boxShadow: '0 4px 24px rgba(139,92,246,0.10)',
              }}>
                <div style={{
                  padding: '10px 16px',
                  background: 'linear-gradient(90deg, #7c3aed, #6d28d9)',
                  display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <span style={{ fontSize: 18 }}>🤖</span>
                  <div>
                    <p style={{ fontSize: 11, fontWeight: 800, color: '#e9d5ff', margin: 0, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                      AI Mentor — Client Change Brief
                    </p>
                    <p style={{ fontSize: 12, fontWeight: 700, color: '#fff', margin: '2px 0 0' }}>
                      {scenario.title}
                    </p>
                  </div>
                </div>

                <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div>
                    <p style={{ fontSize: 10, fontWeight: 800, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 5px' }}>
                      📣 What the client wants
                    </p>
                    <p style={{ fontSize: 12, color: '#3b0764', lineHeight: 1.6, margin: 0, padding: '8px 12px', background: 'rgba(139,92,246,0.07)', borderRadius: 8, border: '1px solid #ddd6fe' }}>
                      {scenario.clientWants}
                    </p>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div style={{ padding: '10px 12px', borderRadius: 10, background: '#fff', border: '1.5px solid #e9d5ff', textAlign: 'center' }}>
                      <p style={{ fontSize: 9, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 4px' }}>⚡ Priority</p>
                      <p style={{ fontSize: 14, fontWeight: 900, color: pm.color, margin: 0 }}>{pm.label}</p>
                    </div>
                    <div style={{ padding: '10px 12px', borderRadius: 10, background: '#fff', border: '1.5px solid #e9d5ff', textAlign: 'center' }}>
                      <p style={{ fontSize: 9, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '0 0 4px' }}>⏰ Expected Time</p>
                      <p style={{ fontSize: 13, fontWeight: 800, color: '#6d28d9', margin: 0 }}>{scenario.expectedTime}</p>
                    </div>
                  </div>

                  <div>
                    <p style={{ fontSize: 10, fontWeight: 800, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.07em', margin: '0 0 6px' }}>
                      🚫 Do NOT break
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                      {scenario.avoidBreaking.map((item, i) => (
                        <div key={i} style={{
                          display: 'flex', alignItems: 'flex-start', gap: 8,
                          padding: '7px 10px', borderRadius: 8,
                          background: '#fff', border: '1px solid #fecaca',
                        }}>
                          <span style={{ fontSize: 12, flexShrink: 0, marginTop: 1 }}>⚠️</span>
                          <span style={{ fontSize: 11.5, color: '#991b1b', lineHeight: 1.45 }}>{item}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* ── Auto-created items status ── */}
              <div style={{ borderRadius: 14, border: '1.5px solid #e5e7eb', overflow: 'hidden' }}>
                <div style={{
                  padding: '10px 14px', background: '#f9fafb',
                  borderBottom: '1px solid #e5e7eb',
                  display: 'flex', alignItems: 'center', gap: 10,
                }}>
                  <span style={{ fontSize: 15 }}>📋</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: '#111827' }}>Automatically Created</span>
                  {triggering && (
                    <span style={{ fontSize: 10, color: '#6b7280', marginLeft: 'auto' }}>Working…</span>
                  )}
                </div>

                {/* Error banner */}
                {triggerError && (
                  <div style={{
                    padding: '10px 14px', background: '#fef2f2', borderBottom: '1px solid #fecaca',
                    fontSize: 11, color: '#991b1b',
                  }}>
                    ⚠️ {triggerError}
                  </div>
                )}

                <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>

                  {/* Task created */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 12px', borderRadius: 10,
                    background: '#faf5ff', border: '1.5px solid #c4b5fd',
                  }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: 8, flexShrink: 0,
                      background: createdTaskId ? '#10b981' : (triggering ? '#f59e0b' : (triggerError ? '#ef4444' : '#f59e0b')),
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      animation: createdTaskId ? 'ccr-check-in 0.3s ease' : 'none',
                    }}>
                      {createdTaskId ? (
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="white" strokeWidth="2.5">
                          <path d="M2.5 7l3 3L11.5 4" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : triggering ? (
                        <div style={{ width: 12, height: 12, borderRadius: '50%', border: '2px solid white', borderTopColor: 'transparent', animation: 'ccr-spin 0.7s linear infinite' }} />
                      ) : (
                        <span style={{ fontSize: 14, color: 'white' }}>{triggerError ? '✕' : '…'}</span>
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <p style={{ fontSize: 12, fontWeight: 700, color: '#1e1b4b', margin: 0 }}>
                        Task Assigned to You
                      </p>
                      <p style={{ fontSize: 11, color: '#6d28d9', margin: '2px 0 0' }}>
                        {createdTaskId
                          ? `[CCR] ${scenario.title}`
                          : triggering ? 'Creating task in your active sprint…' : triggerError ? 'Failed — check error above' : 'Pending…'}
                      </p>
                    </div>
                    {createdTaskId && (
                      <span style={{
                        fontSize: 9, fontWeight: 800, padding: '2px 8px', borderRadius: 99,
                        background: '#ede9fe', color: '#6d28d9', border: '1px solid #c4b5fd',
                      }}>PURPLE IN KANBAN</span>
                    )}
                  </div>

                  {/* Ticket created */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 12px', borderRadius: 10,
                    background: '#f0f9ff', border: '1.5px solid #bae6fd',
                  }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: 8, flexShrink: 0,
                      background: createdTicketId ? '#10b981' : (triggering ? '#f59e0b' : (triggerError ? '#ef4444' : '#f59e0b')),
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      animation: createdTicketId ? 'ccr-check-in 0.3s ease 0.1s both' : 'none',
                    }}>
                      {createdTicketId ? (
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="white" strokeWidth="2.5">
                          <path d="M2.5 7l3 3L11.5 4" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : triggering ? (
                        <div style={{ width: 12, height: 12, borderRadius: '50%', border: '2px solid white', borderTopColor: 'transparent', animation: 'ccr-spin 0.7s linear infinite' }} />
                      ) : (
                        <span style={{ fontSize: 14, color: 'white' }}>{triggerError ? '✕' : '…'}</span>
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <p style={{ fontSize: 12, fontWeight: 700, color: '#0369a1', margin: 0 }}>
                        Incoming Ticket for Your Team
                      </p>
                      <p style={{ fontSize: 11, color: '#0284c7', margin: '2px 0 0' }}>
                        {createdTicketId
                          ? `Ticket ID: ${createdTicketId.slice(0, 8)}… (visible in your Incoming tickets)`
                          : triggering ? 'Creating ticket…' : triggerError ? 'Failed' : 'Pending…'}
                      </p>
                    </div>
                  </div>

                  {/* Notification sent */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 12px', borderRadius: 10,
                    background: '#f0fdf4', border: '1.5px solid #86efac',
                  }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: 8, flexShrink: 0,
                      background: createdTaskId ? '#10b981' : (triggering ? '#f59e0b' : '#f59e0b'),
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      animation: createdTaskId ? 'ccr-check-in 0.3s ease 0.2s both' : 'none',
                    }}>
                      {createdTaskId ? (
                        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="white" strokeWidth="2.5">
                          <path d="M2.5 7l3 3L11.5 4" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : (
                        <div style={{ width: 12, height: 12, borderRadius: '50%', border: '2px solid white', borderTopColor: 'transparent', animation: 'ccr-spin 0.7s linear infinite' }} />
                      )}
                    </div>
                    <div style={{ flex: 1 }}>
                      <p style={{ fontSize: 12, fontWeight: 700, color: '#166534', margin: 0 }}>
                        Teammates Notified
                      </p>
                      <p style={{ fontSize: 11, color: '#15803d', margin: '2px 0 0' }}>
                        {createdTaskId ? 'Same-role teammates alerted 🔔' : 'Sending…'}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Sprint impact */}
              <div style={{
                padding: '10px 14px', borderRadius: 10,
                background: '#fef2f2', border: '1px solid #fecaca',
                fontSize: 12, color: '#991b1b', lineHeight: 1.5,
              }}>
                ⚠️ <strong>Sprint Impact:</strong> {scenario.sprintImpact} added to your current sprint timeline. The new task will appear in your Kanban board with a <strong>purple highlight</strong>.
              </div>

              {/* What to do next */}
              <div style={{
                padding: '14px 16px', borderRadius: 12,
                background: '#f0f9ff', border: '1px solid #bae6fd',
              }}>
                <p style={{ fontSize: 12, fontWeight: 700, color: '#0369a1', margin: '0 0 8px' }}>📋 What to do next</p>
                {[
                  'Head to your Kanban board — the new purple task is now in your active sprint',
                  'Check the notification bell for the full AI Mentor brief',
                  'Mention this in your next standup under "blockers / new items"',
                  'Your same-role teammates also received a heads-up notification',
                ].map((step, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 12, color: '#0284c7', flexShrink: 0, marginTop: 1 }}>→</span>
                    <span style={{ fontSize: 12, color: '#0c4a6e', lineHeight: 1.5 }}>{step}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* ── Footer ── */}
            <div style={{
              padding: '14px 20px', borderTop: '1px solid #e5e7eb',
              background: '#f9fafb', flexShrink: 0,
              display: 'flex', justifyContent: 'flex-end', gap: 10,
            }}>
              <button onClick={() => { deactivateMode() }} style={{
                padding: '10px 20px', borderRadius: 10,
                background: 'linear-gradient(135deg, #7c3aed, #6d28d9)',
                color: '#fff', fontSize: 13, fontWeight: 700,
                border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                boxShadow: '0 4px 12px rgba(109,40,217,0.35)',
              }}>
                Got it — Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}