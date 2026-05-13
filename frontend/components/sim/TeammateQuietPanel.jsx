'use client'

/**
 * TeammateQuietPanel.jsx
 *
 * FIXES:
 * 1. On mount, checks /state first. If a sim is already active (tasks already
 *    reassigned), shows the existing state instead of re-running /activate and
 *    getting 0 tasks because they've already moved.
 * 2. handleDeactivate waits for the backend to fully complete before closing
 *    the panel, so tasks are actually restored before the UI disappears.
 * 3. After deactivate, fires a custom event so any task list on the page
 *    re-fetches automatically — no manual refresh needed.
 */

import { useEffect, useState, useCallback } from 'react'
import { useSimMode } from '@/lib/store/simModeStore'
import api from '@/lib/api'

// ─── Palette ──────────────────────────────────────────────────────────────────
const C = {
  blue:       '#3b82f6',
  blueSoft:   '#eff6ff',
  blueBorder: '#bfdbfe',
  amber:      '#f59e0b',
  amberSoft:  '#fffbeb',
  red:        '#ef4444',
  redSoft:    '#fff1f2',
  green:      '#22c55e',
  greenSoft:  '#f0fdf4',
  ink:        '#111827',
  inkSoft:    '#6b7280',
  border:     '#e5e7eb',
  surface:    '#ffffff',
  surface2:   '#f9fafb',
}

const RISK_COLOR = {
  low:      { color: '#22c55e', bg: '#f0fdf4' },
  medium:   { color: '#f59e0b', bg: '#fffbeb' },
  high:     { color: '#ef4444', bg: '#fff1f2' },
  critical: { color: '#7c3aed', bg: '#fdf4ff' },
}

const STATUS_LABEL = {
  todo:        '📌 To Do',
  in_progress: '🔄 In Progress',
  review:      '👀 In Review',
  done:        '✅ Done',
  paused:      '⏸ Paused',
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Avatar({ src, name, size = 40 }) {
  const colors = ['#5b4fff','#3b82f6','#00c896','#f59e0b','#ec4899','#8b5cf6']
  const bg = colors[(name?.charCodeAt(0) || 0) % colors.length]
  const initials = (name || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
  if (src) return (
    <img src={src} alt={name || ''} width={size} height={size}
      style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover', flexShrink: 0 }} />
  )
  return (
    <div style={{
      width: size, height: size, borderRadius: '50%', background: bg,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: '#fff', fontWeight: 700, fontSize: size * 0.33, flexShrink: 0,
    }}>{initials}</div>
  )
}

function TaskCard({ task, onPickUp, pickingUp, alreadyPickedUp }) {
  const risk = RISK_COLOR[task.priority] ?? RISK_COLOR.medium
  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10,
      padding: '10px 12px', display: 'flex', alignItems: 'flex-start',
      justifyContent: 'space-between', gap: 10,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 13, color: C.ink, marginBottom: 3,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {task.title}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11, color: C.inkSoft }}>
            {STATUS_LABEL[task.status] ?? task.status}
          </span>
          {task.priority && (
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '1px 6px',
              borderRadius: 4, background: risk.bg, color: risk.color,
            }}>
              {task.priority.toUpperCase()}
            </span>
          )}
          {task.intern_role && (
            <span style={{
              fontSize: 10, color: C.inkSoft, background: C.surface2,
              padding: '1px 6px', borderRadius: 4, fontWeight: 600,
            }}>
              {task.intern_role}
            </span>
          )}
        </div>
      </div>
      {onPickUp && (
        <button
          onClick={() => onPickUp(task.id, task.title)}
          disabled={pickingUp === task.id || alreadyPickedUp.has(task.id)}
          style={{
            padding: '5px 10px', borderRadius: 7, fontSize: 11, fontWeight: 700,
            border: 'none', cursor: alreadyPickedUp.has(task.id) ? 'default' : 'pointer',
            background: alreadyPickedUp.has(task.id) ? C.greenSoft : C.blueSoft,
            color: alreadyPickedUp.has(task.id) ? C.green : C.blue,
            flexShrink: 0, whiteSpace: 'nowrap',
            opacity: pickingUp === task.id ? 0.6 : 1,
            transition: 'all 0.15s',
          }}
        >
          {alreadyPickedUp.has(task.id) ? '✓ Picked Up' : pickingUp === task.id ? '…' : 'Pick Up'}
        </button>
      )}
    </div>
  )
}

function Section({ title, children, count }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 12, color: C.inkSoft, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          {title}
        </span>
        {count !== undefined && (
          <span style={{
            fontSize: 10, fontWeight: 800, padding: '1px 6px',
            borderRadius: 20, background: C.blueSoft, color: C.blue,
          }}>{count}</span>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {children}
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function TeammateQuietPanel() {
  const { setActiveMode } = useSimMode()

  const [phase, setPhase]           = useState('loading')
  const [simData, setSimData]       = useState(null)
  const [error, setError]           = useState(null)
  const [escalating, setEscalating] = useState(false)
  const [escalated, setEscalated]   = useState(false)
  const [ticketId, setTicketId]     = useState(null)
  const [pickingUp, setPickingUp]           = useState(null)
  const [pickedUp, setPickedUp]             = useState(new Set())
  const [autoReassigning, setAutoReassigning]   = useState(false)
  const [autoReassignResult, setAutoReassignResult] = useState(null)
  const [deactivating, setDeactivating]     = useState(false)
  const [toast, setToast]                   = useState(null)

  const showToast = useCallback((msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }, [])

  // ── FIX 1: Check /state first, only /activate if no sim is running ──────────
  // Without this, every mount calls /activate again. If tasks have already been
  // reassigned away from the inactive user, /activate finds 0 tasks and shows
  // "No open tasks found" — because they're now assigned to active teammates.
  useEffect(() => {
    let cancelled = false

    async function init() {
      try {
        // First check if a sim is already running
        const stateData = await api.get('/api/sim/tgq/state')

        if (cancelled) return

        if (stateData?.active) {
          // Sim already running — use existing state, don't re-activate
          // Map state response fields to the same shape as activate response
          setSimData({
            inactive_teammate:  stateData.inactive_teammate,
            their_tasks:        stateData.their_tasks ?? [],
            blocked_tasks:      stateData.blocked_tasks ?? [],
            sprint_risk:        stateData.sprint_risk ?? 'high',
            missed_standups:    stateData.missed_standups ?? 2,
            last_seen_hours_ago: stateData.last_seen_hours_ago ?? 48,
          })
          if (stateData.ticket_id) {
            setTicketId(stateData.ticket_id)
            setEscalated(true)
          }
          setPhase('active')
        } else {
          // No sim running — activate a new one
          const data = await api.post('/api/sim/tgq/activate')
          if (!cancelled) {
            setSimData(data)
            setPhase('active')
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e?.message ?? 'Failed to start simulation.')
          setPhase('error')
        }
      }
    }

    init()
    return () => { cancelled = true }
  }, [])

  const handleEscalate = async () => {
    if (escalating || escalated) return
    setEscalating(true)
    try {
      const inactive = simData?.inactive_teammate
      const res = await api.post('/api/sim/tgq/escalate', {
        message: `Escalated via AI Scrum Master. ${inactive?.name ?? 'Teammate'} has missed ${simData?.missed_standups ?? 2} standups.`,
      })
      setTicketId(res.ticket_id)
      setEscalated(true)
      showToast('Escalation ticket created! Teammates notified.', 'success')
    } catch (e) {
      showToast(e?.message ?? 'Failed to create ticket.', 'error')
    } finally {
      setEscalating(false)
    }
  }

  const handleAutoReassign = async () => {
    if (autoReassigning || autoReassignResult) return
    setAutoReassigning(true)
    try {
      const res = await api.post('/api/sim/tgq/auto-reassign')
      setAutoReassignResult(res)
      const reassignedIds = new Set(res.reassigned?.map(r => r.task_id) ?? [])
      setPickedUp(prev => new Set([...prev, ...reassignedIds]))

      // Clear the their_tasks list in the UI since they've now been moved
      setSimData(prev => ({ ...prev, their_tasks: [] }))

      if ((res.reassigned?.length ?? 0) === 0) {
        showToast(res.message ?? 'No tasks found to reassign.', 'info')
      } else {
        showToast(res.message ?? `${res.reassigned.length} tasks redistributed!`, 'success')
      }
    } catch (e) {
      showToast(e?.message ?? 'Failed to auto-reassign tasks.', 'error')
    } finally {
      setAutoReassigning(false)
    }
  }

  const handlePickUp = async (taskId, taskTitle) => {
    if (pickingUp || pickedUp.has(taskId)) return
    setPickingUp(taskId)
    try {
      await api.post('/api/sim/tgq/reassign', { task_id: taskId })
      setPickedUp(prev => new Set([...prev, taskId]))
      // Remove from their_tasks list in UI
      setSimData(prev => ({
        ...prev,
        their_tasks: (prev?.their_tasks ?? []).filter(t => t.id !== taskId),
      }))
      showToast(`"${taskTitle}" is now yours. Sprint can continue!`, 'success')
    } catch (e) {
      showToast(e?.message ?? 'Failed to reassign task.', 'error')
    } finally {
      setPickingUp(null)
    }
  }

  // ── FIX 2: Wait for backend to complete before closing the panel ────────────
  // The old code called setActiveMode(null) immediately after the fetch returned,
  // but the DB write for task restoration hadn't finished yet. This caused tasks
  // to stay reassigned until a manual refresh or a second deactivate call.
  const handleDeactivate = async () => {
    if (deactivating) return
    setDeactivating(true)
    try {
      const res = await api.post('/api/sim/tgq/deactivate')

      if (res?.failed_count > 0 && res?.restored_count === 0) {
        // Complete failure — likely an RLS / service key issue
        showToast(`⚠️ ${res.failed_count} task(s) could not be returned. Check backend logs.`, 'error')
        setDeactivating(false)
        return
      }

      const msg = res?.restored_count > 0
        ? `✅ Sim ended — ${res.restored_count} task(s) returned to ${simData?.inactive_teammate?.name ?? 'teammate'}.`
        : 'Simulation ended.'
      showToast(msg, 'success')

      // FIX 3: Notify task list pages to re-fetch without a manual refresh.
      // Any component can listen to this event with:
      //   window.addEventListener('tgq:sim_ended', () => refetch())
      try {
        window.dispatchEvent(new CustomEvent('tgq:sim_ended', {
          detail: { restored_count: res?.restored_count ?? 0 }
        }))
      } catch {}

      // Short pause so the toast is visible before the panel closes
      await new Promise(r => setTimeout(r, 1200))
      setActiveMode(null)

    } catch (e) {
      showToast(e?.message ?? 'Failed to end simulation.', 'error')
      setDeactivating(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  const riskInfo = RISK_COLOR[simData?.sprint_risk] ?? RISK_COLOR.high
  const inactive = simData?.inactive_teammate

  const panelStyle = {
    margin: '0 0 0 68px',
    padding: '0 20px 16px',
  }

  const cardStyle = {
    background: C.surface,
    border: `1.5px solid ${C.blueBorder}`,
    borderRadius: 16,
    padding: '16px 20px',
    boxShadow: '0 4px 24px rgba(59,130,246,0.08)',
  }

  if (phase === 'loading') {
    return (
      <div style={panelStyle}>
        <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: C.blue,
            animation: 'tgq-pulse 1.4s ease-in-out infinite' }} />
          <style>{`@keyframes tgq-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.5)}}`}</style>
          <span style={{ fontSize: 13, color: C.inkSoft }}>AI Scrum Master is analysing team activity…</span>
        </div>
      </div>
    )
  }

  if (phase === 'error') {
    return (
      <div style={panelStyle}>
        <div style={{ ...cardStyle, borderColor: '#fecaca', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 13, color: C.red }}>⚠ {error}</span>
          <button onClick={() => setActiveMode(null)} style={{
            fontSize: 11, padding: '4px 10px', borderRadius: 7,
            background: C.surface2, border: `1px solid ${C.border}`,
            cursor: 'pointer', color: C.inkSoft,
          }}>Dismiss</button>
        </div>
      </div>
    )
  }

  return (
    <div style={panelStyle}>
      <style>{`
        @keyframes tgq-pulse {
          0%,100% { opacity:1; transform:scale(1) }
          50% { opacity:.5; transform:scale(1.5) }
        }
        .tgq-btn {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 7px 14px; border-radius: 9px; font-size: 12px;
          font-weight: 700; border: none; cursor: pointer;
          font-family: inherit; transition: opacity 0.15s, transform 0.1s;
        }
        .tgq-btn:active { transform: scale(0.97); }
        .tgq-btn:disabled { opacity: 0.55; cursor: default; }
      `}</style>

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
          padding: '10px 16px', borderRadius: 12, fontSize: 13, fontWeight: 600,
          background: toast.type === 'success' ? C.greenSoft : toast.type === 'error' ? C.redSoft : C.blueSoft,
          color: toast.type === 'success' ? C.green : toast.type === 'error' ? C.red : C.blue,
          border: `1px solid ${toast.type === 'success' ? '#bbf7d0' : toast.type === 'error' ? '#fecaca' : C.blueBorder}`,
          boxShadow: '0 4px 20px rgba(0,0,0,0.1)',
          maxWidth: 320,
        }}>
          {toast.msg}
        </div>
      )}

      <div style={cardStyle}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14, gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
              borderRadius: 8, background: C.blueSoft, border: `1px solid ${C.blueBorder}`, flexShrink: 0,
            }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: C.blue,
                animation: 'tgq-pulse 1.6s ease-in-out infinite' }} />
              <span style={{ fontSize: 11, fontWeight: 800, color: C.blue, letterSpacing: '0.05em' }}>TGQ</span>
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 800, fontSize: 14, color: C.ink }}>Teammate Goes Quiet</div>
              <div style={{ fontSize: 11, color: C.inkSoft, marginTop: 1 }}>
                AI Scrum Master detected inactivity · Sprint at{' '}
                <span style={{ fontWeight: 700, color: riskInfo.color }}>
                  {(simData?.sprint_risk ?? 'high').toUpperCase()} RISK
                </span>
              </div>
            </div>
          </div>

          <button
            className="tgq-btn"
            onClick={handleDeactivate}
            disabled={deactivating}
            style={{ background: C.surface2, color: C.inkSoft, border: `1px solid ${C.border}`, flexShrink: 0 }}
          >
            {deactivating ? 'Ending…' : 'End Sim'}
          </button>
        </div>

        {/* Inactive teammate callout */}
        {inactive && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 12,
            padding: '12px 14px', borderRadius: 12,
            background: C.surface2, border: `1px solid ${C.border}`,
            marginBottom: 16,
          }}>
            <div style={{ position: 'relative', flexShrink: 0 }}>
              <Avatar src={inactive.avatar_url} name={inactive.name} size={44} />
              <div style={{
                position: 'absolute', bottom: 0, right: 0,
                width: 13, height: 13, borderRadius: '50%',
                background: '#9ca3af', border: '2px solid #fff',
              }} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: C.ink }}>{inactive.name}</div>
              <div style={{ fontSize: 11, color: C.inkSoft, marginTop: 1 }}>
                {inactive.intern_role?.replace('_', ' ')} · 🔕 No standup for {simData?.missed_standups ?? 2} days ·
                Last seen {simData?.last_seen_hours_ago ?? 48}h ago
              </div>
            </div>
            <div style={{
              padding: '4px 10px', borderRadius: 8, fontSize: 11, fontWeight: 700,
              background: riskInfo.bg, color: riskInfo.color, flexShrink: 0,
            }}>
              {simData?.sprint_risk?.toUpperCase()}
            </div>
          </div>
        )}

        {/* Auto-Reassign Result */}
        {autoReassignResult && (
          <div style={{
            background: '#f0fdf4', border: '1px solid #bbf7d0',
            borderRadius: 10, padding: '12px 14px', marginBottom: 14,
          }}>
            <div style={{ fontWeight: 700, fontSize: 12, color: '#166534', marginBottom: 8 }}>
              ✅ Tasks Auto-Redistributed ({autoReassignResult.reassigned?.length ?? 0}/{autoReassignResult.total ?? 0})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {(autoReassignResult.reassigned ?? []).map(r => (
                <div key={r.task_id} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontSize: 11, color: '#166534',
                }}>
                  <span style={{
                    background: '#dcfce7', borderRadius: 4, padding: '1px 6px',
                    fontWeight: 700, flexShrink: 0,
                  }}>{r.assignee_name}</span>
                  <span style={{ color: '#4b5563' }}>←</span>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.task_title}
                  </span>
                </div>
              ))}
              {(autoReassignResult.failed ?? []).length > 0 && (
                <div style={{ fontSize: 11, color: '#b91c1c', marginTop: 4 }}>
                  ⚠ {autoReassignResult.failed.length} task(s) could not be reassigned.
                </div>
              )}
            </div>
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button
            className="tgq-btn"
            onClick={handleEscalate}
            disabled={escalating || escalated}
            style={{
              background: escalated ? C.greenSoft : C.blueSoft,
              color: escalated ? C.green : C.blue,
            }}
          >
            {escalated ? `✓ Ticket Created` : escalating ? 'Raising…' : '🎫 Raise Escalation Ticket'}
          </button>

          <button
            className="tgq-btn"
            onClick={handleAutoReassign}
            disabled={autoReassigning || !!autoReassignResult}
            style={{
              background: autoReassignResult ? C.greenSoft : C.amberSoft,
              color: autoReassignResult ? C.green : C.amber,
              border: `1px solid ${autoReassignResult ? '#bbf7d0' : '#fde68a'}`,
            }}
          >
            {autoReassignResult
              ? `✓ All Tasks Redistributed`
              : autoReassigning
                ? 'Reassigning…'
                : '🔄 Auto-Reassign All Tasks'}
          </button>

          {escalated && ticketId && (
            <a
              href={`/dashboard/ticket/${ticketId}`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '7px 14px', borderRadius: 9, fontSize: 12,
                fontWeight: 700, textDecoration: 'none',
                background: C.surface2, color: C.inkSoft,
                border: `1px solid ${C.border}`,
              }}
            >
              View Ticket →
            </a>
          )}

          <div style={{ flex: 1 }} />

          <div style={{ fontSize: 11, color: C.inkSoft, alignSelf: 'center' }}>
            {pickedUp.size > 0 && `✓ ${pickedUp.size} task${pickedUp.size > 1 ? 's' : ''} picked up`}
          </div>
        </div>
      </div>
    </div>
  )
}