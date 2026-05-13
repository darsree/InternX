'use client'
// frontend/components/sim/ProductionIncidentPanel.jsx

import { useEffect, useState, useCallback, useRef } from 'react'
import { useAuthStore } from '@/lib/store/authStore'
import api from '@/lib/api'

const PROJECT_ID  = 'aaaaaaaa-0000-0000-0000-000000000001'
const GROUP_ID    = 'bbbbbbbb-0000-0000-0000-000000000001'
const SPRINT_ID   = 'ffffffff-be00-0000-0000-000000000001'
const INCIDENT_ID = 'f0000001-0000-0000-0000-000000000001'

const INCIDENT_SCENARIO =
  'Race condition in POST /api/orders — duplicate orders & negative stock_quantity in production'

const REQUIRED_ROLES = ['backend', 'frontend', 'tester']

const ROLE_COLORS = {
  backend:  { color: '#3b82f6', bg: '#eff6ff', border: '#bfdbfe' },
  frontend: { color: '#5b4fff', bg: '#ede9ff', border: '#c4b5fd' },
  tester:   { color: '#8b5cf6', bg: '#f5f3ff', border: '#ddd6fe' },
}

function formatTime(secs) {
  if (secs <= 0) return '00:00'
  const m = Math.floor(secs / 60).toString().padStart(2, '0')
  const s = (secs % 60).toString().padStart(2, '0')
  return `${m}:${s}`
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared incident state — exported so any component can read it without
// prop-drilling. Import { useIncidentState } wherever you need it.
// ─────────────────────────────────────────────────────────────────────────────
let _listeners = []
let _sharedState = { active: false, incident: null, hotfixTaskIds: [] }

export function useIncidentState() {
  const [state, setState] = useState(_sharedState)
  useEffect(() => {
    _listeners.push(setState)
    setState(_sharedState)
    return () => { _listeners = _listeners.filter(l => l !== setState) }
  }, [])
  return state
}

function broadcast(next) {
  _sharedState = next
  _listeners.forEach(l => l(next))
}

// ─────────────────────────────────────────────────────────────────────────────
export default function ProductionIncidentPanel() {
  const { user } = useAuthStore()
  const [incident,    setIncident]    = useState(null)
  const [secondsLeft, setSecondsLeft] = useState(null)
  const [loading,     setLoading]     = useState(true)
  const [triggering,  setTriggering]  = useState(false)
  const [resolving,   setResolving]   = useState(false)
  const [ending,      setEnding]      = useState(false)
  const [expanded,    setExpanded]    = useState(false)
  const [endConfirm,  setEndConfirm]  = useState(false)
  const [postmortem,  setPostmortem]  = useState(null)   // postmortem data after resolution
  const [showPostmortem, setShowPostmortem] = useState(false)
  const timerRef        = useRef(null)
  const autoResolvedRef = useRef(false)

  // ── Fetch — polls every 10 s ──────────────────────────────────────────────
  const fetchIncident = useCallback(async () => {
    try {
      const res = await api.get(`/api/incidents/active?project_id=${PROJECT_ID}`)
      const inc = res.data.incident ?? null
      setIncident(inc)
      if (inc) setSecondsLeft(inc.seconds_remaining)
      broadcast({
        active: !!inc,
        incident: inc,
        hotfixTaskIds: (inc?.tasks ?? [])
          .filter(t => t.title?.startsWith('[HOTFIX]'))
          .map(t => t.id),
      })
      setLoading(false)
    } catch { setLoading(false) }
  }, [])

  useEffect(() => {
    fetchIncident()
    const poll = setInterval(fetchIncident, 10_000)
    return () => clearInterval(poll)
  }, [fetchIncident])

  // Client-side countdown
  useEffect(() => {
    clearInterval(timerRef.current)
    if (secondsLeft === null) return
    timerRef.current = setInterval(() => setSecondsLeft(s => s > 0 ? s - 1 : 0), 1000)
    return () => clearInterval(timerRef.current)
  }, [secondsLeft])

  const hotfixTasks = (incident?.tasks ?? []).filter(
    t => t.incident_id === incident?.id && t.title?.startsWith('[HOTFIX]')
  )
  const doneCount     = hotfixTasks.filter(t => t.status === 'done' || t.github_pr_url).length
  const allHotfixDone = hotfixTasks.length >= REQUIRED_ROLES.length && doneCount === hotfixTasks.length

  // ── Fetch postmortem ──────────────────────────────────────────────────────
  const fetchPostmortem = useCallback(async (incidentId) => {
    try {
      const res = await api.get(`/api/incidents/${incidentId}/postmortem`)
      setPostmortem(res.data)
      setShowPostmortem(true)
    } catch (e) { console.error('Postmortem fetch failed', e) }
  }, [])

  // ── Resolve ───────────────────────────────────────────────────────────────
  const resolveIncident = useCallback(async (id) => {
    if (!id) return
    setResolving(true)
    try {
      await api.patch(`/api/incidents/${id}/resolve`)
      await fetchPostmortem(id)
      setIncident(null); setSecondsLeft(null); setExpanded(false)
      broadcast({ active: false, incident: null, hotfixTaskIds: [] })
    } catch (e) { console.error('Resolve failed', e) }
    finally { setResolving(false) }
  }, [fetchPostmortem])

  // Auto-resolve when all hotfixes done
  useEffect(() => {
    if (!incident || !allHotfixDone || autoResolvedRef.current) return
    autoResolvedRef.current = true
    resolveIncident(incident.id)
  }, [allHotfixDone, incident, resolveIncident])
  useEffect(() => { if (incident) autoResolvedRef.current = false }, [incident?.id])

  // ── End Mode ──────────────────────────────────────────────────────────────
  const endMode = async () => {
    if (!incident) return
    setEnding(true)
    try {
      await api.patch(`/api/incidents/${incident.id}/end-mode`)
      await fetchPostmortem(incident.id)
      setIncident(null); setSecondsLeft(null); setExpanded(false); setEndConfirm(false)
      broadcast({ active: false, incident: null, hotfixTaskIds: [] })
    } catch (e) { console.error('End mode failed', e) }
    finally { setEnding(false) }
  }

  // ── Trigger ───────────────────────────────────────────────────────────────
  const triggerIncident = async () => {
    setTriggering(true)
    try {
      await api.post('/api/incidents/trigger', {
        incident_id: INCIDENT_ID, project_id: PROJECT_ID,
        group_id: GROUP_ID, sprint_id: SPRINT_ID,
      })
      await fetchIncident()
      setExpanded(true)
    } catch (e) { console.error('Trigger failed', e) }
    finally { setTriggering(false) }
  }

  if (loading) return null

  const isAdmin    = user?.role === 'admin' || user?.role === 'mentor'
  const slaMinutes = incident?.sla_minutes ?? 90
  const slaPercent = incident
    ? Math.max(0, Math.min(100, (secondsLeft / (slaMinutes * 60)) * 100))
    : 100
  const isCritical = secondsLeft !== null && secondsLeft < 600

  const roleBadges = REQUIRED_ROLES.map(role => {
    const task = hotfixTasks.find(t => t.intern_role === role)
    const done = task?.status === 'done' || !!task?.github_pr_url
    const inProgress = task?.status === 'in_progress' || task?.status === 'review'
    return { role, done, inProgress, task }
  })

  const CSS = `
    @keyframes sev1-pulse { 0%,100%{opacity:1} 50%{opacity:.6} }
    @keyframes sev1-glow  { 0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.4)} 50%{box-shadow:0 0 0 6px rgba(239,68,68,0)} }
    @keyframes sev1-slide  { from{opacity:0;transform:translateY(-8px)} to{opacity:1;transform:translateY(0)} }
    @keyframes sev1-shimmer { 0%{background-position:-200% 0} 100%{background-position:200% 0} }

    .inc-root {
      position: sticky; top: 0; z-index: 40;
      font-family: inherit;
    }

    /* ── Compact bar ── */
    .inc-bar {
      display: flex; align-items: center; gap: 10px;
      padding: 0 20px 0 76px; min-height: 48px;
      background: ${incident
        ? 'linear-gradient(90deg, #fff1f2 0%, #fff5f5 100%)'
        : '#f9fafb'};
      border-bottom: 2px solid ${incident ? '#fecaca' : '#e5e7eb'};
      transition: background .3s, border-color .3s;
      ${incident ? 'animation: sev1-pulse 3s ease-in-out infinite;' : ''}
    }

    /* SLA progress line at very top */
    .inc-sla-track {
      position: absolute; top: 0; left: 0; right: 0; height: 3px;
      background: #fecaca; overflow: hidden;
    }
    .inc-sla-fill {
      height: 100%; border-radius: 0 2px 2px 0;
      background: ${isCritical
        ? 'linear-gradient(90deg,#ef4444,#dc2626)'
        : 'linear-gradient(90deg,#f59e0b,#fbbf24)'};
      transition: width 1s linear, background .4s;
    }

    /* Live dot */
    .inc-live-dot {
      width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
      background: ${incident ? '#ef4444' : '#9ca3af'};
      ${incident ? 'animation: sev1-glow 1.5s ease-in-out infinite;' : ''}
    }

    /* SEV badge */
    .inc-sev-badge {
      font-size: 10px; font-weight: 800; letter-spacing: .08em;
      padding: 3px 8px; border-radius: 5px; flex-shrink: 0;
      background: #fee2e2; color: #dc2626;
      border: 1px solid #fecaca;
    }

    .inc-separator {
      width: 1px; height: 16px; background: #fecaca; flex-shrink: 0;
    }

    /* Role pills in bar */
    .inc-role-pill {
      display: inline-flex; align-items: center; gap: 4px;
      font-size: 10px; font-weight: 700; padding: 2px 8px 2px 6px;
      border-radius: 20px; flex-shrink: 0; letter-spacing: .03em;
      transition: transform .15s;
    }
    .inc-role-pill-dot {
      width: 5px; height: 5px; border-radius: 50%;
    }

    /* Timer */
    .inc-timer {
      font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
      font-size: 15px; font-weight: 700; letter-spacing: .08em; flex-shrink: 0;
      color: ${isCritical ? '#dc2626' : '#d97706'};
      ${isCritical ? 'animation: sev1-pulse 1s ease-in-out infinite;' : ''}
    }

    /* Buttons */
    .inc-btn {
      font-size: 12px; font-weight: 600; padding: 5px 13px; border-radius: 8px;
      cursor: pointer; border: none; transition: all .15s; font-family: inherit;
      flex-shrink: 0; white-space: nowrap;
    }
    .inc-btn:hover:not(:disabled) { transform: translateY(-1px); filter: brightness(1.05); }
    .inc-btn:active:not(:disabled) { transform: translateY(0); }
    .inc-btn:disabled { opacity: .5; cursor: not-allowed; }
    .inc-btn-red    { background: #ef4444; color: white; box-shadow: 0 2px 8px rgba(239,68,68,.3); }
    .inc-btn-green  { background: #22c55e; color: white; box-shadow: 0 2px 8px rgba(34,197,94,.3); }
    .inc-btn-ghost  { background: white; color: #6b7280; border: 1px solid #e5e7eb; }
    .inc-btn-dark   { background: #1e1b4b; color: white; box-shadow: 0 2px 8px rgba(30,27,75,.3); }

    /* Sprint gate pill */
    .inc-sprint-gate {
      display: inline-flex; align-items: center; gap: 5px;
      font-size: 10px; font-weight: 700; padding: 3px 9px; border-radius: 20px;
      flex-shrink: 0; letter-spacing: .03em;
      background: #fef2f2; color: #dc2626; border: 1px solid #fecaca;
      transition: all .3s;
    }
    .inc-sprint-gate.unlocked {
      background: #f0fdf4; color: #16a34a; border-color: #bbf7d0;
    }

    /* ── Expanded panel ── */
    .inc-expanded {
      background: white;
      border-bottom: 2px solid #fecaca;
      animation: sev1-slide .22s ease;
      overflow: hidden;
    }
    .inc-expanded-inner {
      padding: 16px 20px 20px 76px;
      display: grid;
      grid-template-columns: 1fr 300px;
      gap: 20px;
    }

    /* Scenario card */
    .inc-scenario-card {
      border-radius: 12px; padding: 16px;
      background: linear-gradient(135deg, #fff5f5 0%, #fffbeb 100%);
      border: 1px solid #fecaca;
    }
    .inc-scenario-header {
      display: flex; align-items: center; gap: 8px;
      margin-bottom: 10px;
    }
    .inc-scenario-icon {
      width: 32px; height: 32px; border-radius: 8px;
      background: #fee2e2; display: flex; align-items: center;
      justify-content: center; font-size: 16px; flex-shrink: 0;
    }
    .inc-instruction-list {
      list-style: none; padding: 0; margin: 0;
    }
    .inc-instruction-list li {
      display: flex; align-items: flex-start; gap: 8px;
      padding: 6px 0; font-size: 12px; color: #374151;
      border-bottom: 1px solid rgba(239,68,68,.08);
    }
    .inc-instruction-list li:last-child { border-bottom: none; }
    .inc-instruction-num {
      width: 18px; height: 18px; border-radius: 50%; flex-shrink: 0;
      background: #fee2e2; color: #dc2626; font-size: 10px; font-weight: 700;
      display: flex; align-items: center; justify-content: center;
    }

    /* Hotfix tasks panel */
    .inc-hotfix-panel {
      display: flex; flex-direction: column; gap: 10px;
    }
    .inc-hotfix-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 2px;
    }
    .inc-progress-track {
      height: 6px; border-radius: 3px; background: #f3f4f6; overflow: hidden;
      margin-bottom: 8px;
    }
    .inc-progress-fill {
      height: 100%; border-radius: 3px;
      background: linear-gradient(90deg, #22c55e, #4ade80);
      transition: width .6s cubic-bezier(.4,0,.2,1);
    }

    /* Hotfix task card */
    .inc-task-card {
      border-radius: 10px; padding: 12px 14px;
      border: 1.5px solid;
      transition: transform .15s, box-shadow .15s;
    }
    .inc-task-card:hover { transform: translateY(-1px); }
    .inc-task-card.pending {
      background: #fafafa; border-color: #e5e7eb;
    }
    .inc-task-card.in-progress {
      background: #fffbeb; border-color: #fde68a;
    }
    .inc-task-card.done {
      background: #f0fdf4; border-color: #bbf7d0;
    }
    .inc-task-card-top {
      display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
    }
    .inc-task-role-badge {
      font-size: 9px; font-weight: 800; padding: 2px 7px;
      border-radius: 4px; letter-spacing: .06em; text-transform: uppercase;
      flex-shrink: 0;
    }
    .inc-task-status-dot {
      width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
      margin-left: auto;
    }
    .inc-task-title {
      font-size: 12px; font-weight: 600; color: #1f2937;
      line-height: 1.4; margin-bottom: 4px;
    }
    .inc-task-status-label {
      font-size: 10px; font-weight: 600; padding: 1px 6px;
      border-radius: 4px; display: inline-block;
    }

    /* No incident state */
    .inc-empty-bar {
      display: flex; align-items: center; gap: 10px; padding: 0 20px 0 76px;
      min-height: 44px; background: #f9fafb; border-bottom: 1.5px solid #e5e7eb;
    }

    /* End confirm modal */
    .inc-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,.5);
      display: flex; align-items: center; justify-content: center;
      z-index: 9999; backdrop-filter: blur(4px);
    }
    .inc-modal {
      background: white; border-radius: 20px; padding: 32px;
      max-width: 440px; width: 90%;
      box-shadow: 0 25px 60px rgba(0,0,0,.2), 0 0 0 1px rgba(0,0,0,.05);
      animation: sev1-slide .2s ease;
    }
    .inc-modal-icon {
      width: 48px; height: 48px; border-radius: 12px;
      background: #fef2f2; display: flex; align-items: center;
      justify-content: center; font-size: 24px; margin-bottom: 16px;
    }
  `

  return (
    <>
      <style>{CSS}</style>

      {/* ── End Mode confirm modal ──────────────────────────────────────────── */}
      {endConfirm && (
        <div className="inc-overlay" onClick={() => setEndConfirm(false)}>
          <div className="inc-modal" onClick={e => e.stopPropagation()}>
            <div className="inc-modal-icon">⛔</div>
            <p style={{ fontSize: 17, fontWeight: 700, color: '#111827', marginBottom: 6 }}>
              End Incident Mode?
            </p>
            <p style={{ fontSize: 13, color: '#6b7280', lineHeight: 1.65, marginBottom: 16 }}>
              This will immediately stop the simulation and:
            </p>
            <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 10, padding: '12px 16px', marginBottom: 20 }}>
              {[
                'Resolve the active SEV-1 incident',
                'Delete all 3 hotfix tasks (backend · frontend · tester)',
                'Restore all paused sprint tasks to their previous status',
                'Notify all project members that the sprint has resumed',
              ].map((item, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 0', fontSize: 12, color: '#374151', borderBottom: i < 3 ? '1px solid rgba(239,68,68,.1)' : 'none' }}>
                  <span style={{ color: '#ef4444', fontWeight: 700, flexShrink: 0 }}>→</span>
                  {item}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button className="inc-btn inc-btn-ghost" onClick={() => setEndConfirm(false)}>Cancel</button>
              <button className="inc-btn inc-btn-dark" onClick={endMode} disabled={ending}>
                {ending ? 'Ending…' : 'Yes, End Incident Mode'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Postmortem Report Modal ──────────────────────────────────────── */}
      {showPostmortem && postmortem && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 10000,
            background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: 16,
          }}
          onClick={() => setShowPostmortem(false)}
        >
          <div
            style={{
              width: '100%', maxWidth: 640, maxHeight: '90vh',
              background: '#fff', borderRadius: 20,
              boxShadow: '0 32px 96px rgba(0,0,0,0.25)',
              display: 'flex', flexDirection: 'column',
              overflow: 'hidden',
              animation: 'sev1-slide .25s ease',
            }}
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div style={{
              padding: '18px 24px 16px',
              background: 'linear-gradient(135deg, #1e1b4b, #312e81)',
              flexShrink: 0,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
                <span style={{ fontSize: 22 }}>📋</span>
                <div style={{ flex: 1 }}>
                  <p style={{ fontSize: 11, fontWeight: 800, color: '#a5b4fc', textTransform: 'uppercase', letterSpacing: '0.08em', margin: 0 }}>
                    Incident Postmortem
                  </p>
                  <p style={{ fontSize: 16, fontWeight: 800, color: '#fff', margin: '2px 0 0', lineHeight: 1.2 }}>
                    SEV-1 Resolved — Team Summary
                  </p>
                </div>
                <button
                  onClick={() => setShowPostmortem(false)}
                  style={{ background: 'rgba(255,255,255,0.1)', border: 'none', borderRadius: 8, width: 32, height: 32, cursor: 'pointer', color: '#a5b4fc', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                >×</button>
              </div>
              {postmortem.elapsed_seconds != null && (() => {
                const m = Math.floor(postmortem.elapsed_seconds / 60)
                const s = postmortem.elapsed_seconds % 60
                return (
                  <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 20, background: 'rgba(255,255,255,0.12)', color: '#e0e7ff' }}>
                      ⏱ Resolved in {m}m {s}s
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 20, background: 'rgba(34,197,94,0.2)', color: '#86efac' }}>
                      ✅ {postmortem.submitted_count}/{postmortem.total_required} members submitted
                    </span>
                  </div>
                )
              })()}
            </div>

            {/* Body — scrollable */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>

              {/* Intro */}
              <div style={{ padding: '10px 14px', borderRadius: 10, background: '#f0f9ff', border: '1px solid #bae6fd', fontSize: 12, color: '#0369a1', lineHeight: 1.6 }}>
                <strong>What is this?</strong> Each team member submitted a postmortem paragraph explaining what caused the production issue from their angle and how they fixed it. This is compiled below as a team incident report.
              </div>

              {/* Per-member summaries */}
              {postmortem.summaries.map((s, i) => {
                const roleColors = {
                  backend:  { color: '#3b82f6', bg: '#eff6ff', border: '#bfdbfe', icon: '⚙️' },
                  frontend: { color: '#5b4fff', bg: '#ede9ff', border: '#c4b5fd', icon: '⚡' },
                  tester:   { color: '#8b5cf6', bg: '#f5f3ff', border: '#ddd6fe', icon: '🧪' },
                }
                const rc = roleColors[s.role] || { color: '#6b7280', bg: '#f3f4f6', border: '#e5e7eb', icon: '👤' }

                return (
                  <div key={s.task_id || i} style={{
                    borderRadius: 14, overflow: 'hidden',
                    border: `1.5px solid ${s.summary ? rc.border : '#e5e7eb'}`,
                  }}>
                    {/* Card header */}
                    <div style={{
                      padding: '10px 14px',
                      background: s.summary ? rc.bg : '#f9fafb',
                      borderBottom: `1px solid ${s.summary ? rc.border : '#e5e7eb'}`,
                      display: 'flex', alignItems: 'center', gap: 10,
                    }}>
                      <span style={{ fontSize: 16 }}>{rc.icon}</span>
                      <div style={{ flex: 1 }}>
                        <p style={{ fontSize: 12, fontWeight: 800, color: rc.color, margin: 0, textTransform: 'capitalize' }}>
                          {s.role} Hotfix
                        </p>
                        <p style={{ fontSize: 11, color: '#6b7280', margin: '1px 0 0' }}>
                          {s.task_title}
                        </p>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                        {s.author && s.summary && (
                          <span style={{ fontSize: 10, fontWeight: 700, color: '#9ca3af' }}>{s.author}</span>
                        )}
                        <span style={{
                          fontSize: 9, fontWeight: 800, padding: '2px 8px', borderRadius: 99,
                          background: s.summary ? '#dcfce7' : '#fef2f2',
                          color: s.summary ? '#16a34a' : '#dc2626',
                          border: `1px solid ${s.summary ? '#86efac' : '#fecaca'}`,
                        }}>
                          {s.summary ? '✓ Submitted' : '○ Pending'}
                        </span>
                      </div>
                    </div>

                    {/* Summary text or placeholder */}
                    <div style={{ padding: '12px 14px', background: '#fff' }}>
                      {s.summary ? (
                        <p style={{ fontSize: 13, color: '#374151', lineHeight: 1.7, margin: 0, whiteSpace: 'pre-wrap' }}>
                          {s.summary}
                        </p>
                      ) : (
                        <p style={{ fontSize: 12, color: '#9ca3af', fontStyle: 'italic', margin: 0 }}>
                          No postmortem submitted yet for this role.
                        </p>
                      )}
                      {s.submitted_at && (
                        <p style={{ fontSize: 10, color: '#d1d5db', marginTop: 8, marginBottom: 0 }}>
                          Submitted {new Date(s.submitted_at).toLocaleString()}
                        </p>
                      )}
                    </div>
                  </div>
                )
              })}

              {postmortem.summaries.length === 0 && (
                <div style={{ textAlign: 'center', padding: '32px 0', color: '#9ca3af', fontSize: 13 }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>📭</div>
                  No hotfix summaries were submitted during this incident.
                </div>
              )}
            </div>

            {/* Footer */}
            <div style={{
              padding: '14px 24px', borderTop: '1px solid #e5e7eb',
              background: '#f9fafb', flexShrink: 0,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <p style={{ fontSize: 11, color: '#9ca3af', margin: 0 }}>
                This postmortem is saved to the incident record.
              </p>
              <button
                onClick={() => setShowPostmortem(false)}
                style={{
                  padding: '9px 22px', borderRadius: 10,
                  background: '#1e1b4b', color: '#fff',
                  fontSize: 13, fontWeight: 700, border: 'none', cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="inc-root">
        {incident && (
          <div className="inc-sla-track" style={{ position: 'relative', zIndex: 1 }}>
            <div className="inc-sla-fill" style={{ width: `${slaPercent}%` }} />
          </div>
        )}

        {/* ── Compact bar ──────────────────────────────────────────────────── */}
        {incident ? (
          <div className="inc-bar" style={{ position: 'relative' }}>
            <div className="inc-live-dot" />
            <span className="inc-sev-badge">SEV-1</span>
            <div className="inc-separator" />
            <span style={{ fontSize: 12, fontWeight: 700, color: '#1f2937', flexShrink: 0 }}>
              ShopSphere Orders
            </span>
            <span style={{ fontSize: 11, color: '#b91c1c', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260, flexShrink: 1 }}>
              {INCIDENT_SCENARIO}
            </span>

            <div className="inc-separator" />

            {/* Role completion pills */}
            {roleBadges.map(({ role, done, inProgress }) => {
              const rc = ROLE_COLORS[role] || { color: '#6b7280', bg: '#f3f4f6', border: '#e5e7eb' }
              return (
                <span key={role} className="inc-role-pill" style={{
                  background: done ? '#f0fdf4' : inProgress ? '#fffbeb' : rc.bg,
                  color: done ? '#16a34a' : inProgress ? '#92400e' : rc.color,
                  border: `1px solid ${done ? '#bbf7d0' : inProgress ? '#fde68a' : rc.border}`,
                }}>
                  <span className="inc-role-pill-dot" style={{
                    background: done ? '#22c55e' : inProgress ? '#f59e0b' : rc.color,
                  }} />
                  {role}
                  <span style={{ opacity: .7 }}>{done ? '✓' : inProgress ? '…' : '○'}</span>
                </span>
              )
            })}

            <span className={`inc-sprint-gate${allHotfixDone ? ' unlocked' : ''}`}>
              {allHotfixDone ? '🟢 Sprint resuming…' : '🔒 Sprint paused'}
            </span>

            <div style={{ flex: 1 }} />

            <span className="inc-timer">{formatTime(secondsLeft)}</span>

            <button className="inc-btn inc-btn-ghost" onClick={() => setExpanded(e => !e)}>
              {expanded ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 8l4-4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                  Hide
                </span>
              ) : (
                <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                  Details
                </span>
              )}
            </button>

            {/* Resolve — always visible for demo purposes */}
            <button className="inc-btn inc-btn-green" onClick={() => resolveIncident(incident.id)} disabled={resolving}>
              {resolving ? 'Resolving…' : '✓ Resolve'}
            </button>

            {/* End Mode — always visible for demo purposes */}
            <button className="inc-btn inc-btn-dark" onClick={() => setEndConfirm(true)}>
              ⛔ End Mode
            </button>
          </div>
        ) : (
          /* ── No active incident ─────────────────────────────────────────── */
          <div className="inc-empty-bar">
            <div className="inc-live-dot" />
            <span style={{ fontSize: 12, color: '#9ca3af', fontWeight: 500 }}>
              Production Incident Mode — no active incident
            </span>
            <div style={{ flex: 1 }} />
            {postmortem && (
              <button className="inc-btn inc-btn-ghost" onClick={() => setShowPostmortem(true)}
                style={{ borderColor: '#c4b5fd', color: '#7c3aed' }}>
                📋 View Postmortem
              </button>
            )}
            <button className="inc-btn inc-btn-red" onClick={triggerIncident} disabled={triggering}>
              {triggering ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <svg style={{ animation: 'spin 1s linear infinite' }} width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <circle cx="6" cy="6" r="4.5" stroke="rgba(255,255,255,.3)" strokeWidth="1.5"/>
                    <path d="M6 1.5A4.5 4.5 0 0110.5 6" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                  Triggering…
                </span>
              ) : '🚨 Declare SEV-1'}
            </button>
          </div>
        )}

        {/* ── Expanded details ─────────────────────────────────────────────── */}
        {incident && expanded && (
          <div className="inc-expanded">
            <div className="inc-expanded-inner">

              {/* Left: Scenario + instructions */}
              <div className="inc-scenario-card">
                <div className="inc-scenario-header">
                  <div className="inc-scenario-icon">🛒</div>
                  <div>
                    <p style={{ fontSize: 13, fontWeight: 700, color: '#dc2626' }}>ShopSphere — Production Incident</p>
                    <p style={{ fontSize: 11, color: '#6b7280' }}>Declared {new Date(incident.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                  </div>
                </div>

                <div style={{ fontSize: 12, color: '#374151', background: 'white', borderRadius: 8, padding: '10px 12px', marginBottom: 12, border: '1px solid rgba(239,68,68,.15)' }}>
                  <strong style={{ color: '#ef4444' }}>Scenario: </strong>{INCIDENT_SCENARIO}
                  <br />
                  <span style={{ color: '#6b7280', fontSize: 11 }}>
                    Impact: Customers double-charged · Inventory at negative stock · Checkout unreliable
                  </span>
                </div>

                <p style={{ fontSize: 11, fontWeight: 700, color: '#92400e', background: '#fffbeb', padding: '6px 10px', borderRadius: 6, border: '1px solid #fde68a', marginBottom: 10 }}>
                  ⚠️ Complete your [HOTFIX] task to unblock your sprint
                </p>

                <ul className="inc-instruction-list">
                  {[
                    'Go to Tasks → your [HOTFIX] task is pinned at the top of To Do',
                    'Work through the task, submit a PR link, and mark it Done',
                    'Regular sprint tasks are locked with a 🔒 overlay until resolved',
                    'Once all 3 roles complete their hotfix, the sprint auto-resumes',
                  ].map((text, i) => (
                    <li key={i}>
                      <span className="inc-instruction-num">{i + 1}</span>
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Right: Hotfix task tracker */}
              <div className="inc-hotfix-panel">
                <div className="inc-hotfix-header">
                  <div>
                    <p style={{ fontSize: 12, fontWeight: 800, color: '#ef4444', letterSpacing: '.06em', textTransform: 'uppercase' }}>
                      Hotfix Tasks
                    </p>
                    <p style={{ fontSize: 11, color: '#9ca3af' }}>Assigned to role members</p>
                  </div>
                  <span style={{ fontSize: 12, fontWeight: 700, color: doneCount === hotfixTasks.length && hotfixTasks.length > 0 ? '#16a34a' : '#374151' }}>
                    {doneCount}/{hotfixTasks.length} done
                  </span>
                </div>

                <div className="inc-progress-track">
                  <div className="inc-progress-fill" style={{
                    width: hotfixTasks.length ? `${(doneCount / hotfixTasks.length) * 100}%` : '0%'
                  }} />
                </div>

                {hotfixTasks.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '20px 0', fontSize: 12, color: '#9ca3af' }}>
                    <div style={{ fontSize: 24, marginBottom: 6 }}>⏳</div>
                    Loading hotfix tasks…
                  </div>
                )}

                {hotfixTasks.map(t => {
                  const done       = t.status === 'done' || !!t.github_pr_url
                  const inProgress = t.status === 'in_progress' || t.status === 'review'
                  const rc = ROLE_COLORS[t.intern_role] || { color: '#6b7280', bg: '#f3f4f6', border: '#e5e7eb' }

                  return (
                    <div key={t.id} className={`inc-task-card ${done ? 'done' : inProgress ? 'in-progress' : 'pending'}`}>
                      <div className="inc-task-card-top">
                        <span className="inc-task-role-badge" style={{
                          background: done ? '#dcfce7' : inProgress ? '#fef9c3' : rc.bg,
                          color:      done ? '#166534' : inProgress ? '#92400e' : rc.color,
                        }}>
                          {t.intern_role}
                        </span>
                        {t.assigned_to && (
                          <span style={{ fontSize: 10, color: '#9ca3af', fontWeight: 500 }}>
                            assigned
                          </span>
                        )}
                        <span className="inc-task-status-dot" style={{
                          background: done ? '#22c55e' : inProgress ? '#f59e0b' : '#d1d5db',
                        }} />
                      </div>
                      <p className="inc-task-title">
                        {t.title.replace('[HOTFIX] ', '')}
                      </p>
                      <span className="inc-task-status-label" style={{
                        background: done ? '#dcfce7' : inProgress ? '#fef9c3' : '#f3f4f6',
                        color:      done ? '#16a34a' : inProgress ? '#92400e' : '#9ca3af',
                      }}>
                        {done
                          ? (t.github_pr_url ? '✓ PR submitted' : '✓ Done')
                          : inProgress
                            ? '⚡ In progress'
                            : '○ Pending'}
                      </span>
                    </div>
                  )
                })}

                {/* Summary gate */}
                <div style={{
                  marginTop: 4, padding: '10px 14px', borderRadius: 10,
                  textAlign: 'center', fontSize: 12, fontWeight: 700,
                  background: allHotfixDone ? '#f0fdf4' : '#fef2f2',
                  border: `1.5px solid ${allHotfixDone ? '#bbf7d0' : '#fecaca'}`,
                  color: allHotfixDone ? '#16a34a' : '#dc2626',
                }}>
                  {allHotfixDone
                    ? '🎉 All hotfixes complete — sprint resuming…'
                    : `🔒 Sprint resumes when all ${REQUIRED_ROLES.length} hotfix tasks are done`}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </>
  )
}