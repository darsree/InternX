'use client'

import { useRouter } from 'next/navigation'
import { StatusBadge, PriorityBadge } from './StatusBadge'
import { MidSprintChangeTag } from './MidSprintChangeBanner'
import { useTaskLock } from '@/components/tasks/useTaskLock'

// ── CCR detection (v1) ────────────────────────────────────────────────────────
function isCCRTask(task) {
  return (
    task.mid_sprint_changed === true &&
    task.mid_sprint_change_reason === 'client_requirement_change'
  )
}

export function TaskCard({ task }) {
  const router = useRouter()

  // ── Derived flags ─────────────────────────────────────────────────────────
  const isCCR     = isCCRTask(task)
  const isOrange  = task.mid_sprint_changed && !isCCR   // generic mid-sprint, non-CCR
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'

  // Production-incident flags (v2)
  const { isLocked, isHotfix, incidentActive } = useTaskLock(task)

  // ── Click handler ─────────────────────────────────────────────────────────
  const handleClick = () => {
    if (isLocked) return
    router.push(`/internship/tasks/${task.id}`)
  }

  // ── Style helpers ─────────────────────────────────────────────────────────
  // Priority order: locked > hotfix > CCR > orange (mid-sprint) > default
  const getBackground = () => {
    if (isHotfix) return '#fff1f2'
    if (isLocked) return '#fafafa'
    if (isCCR)    return '#faf5ff'
    if (isOrange) return '#fff7ed'
    return 'white'
  }

  const getBorderColor = (hovered = false) => {
    if (isLocked)  return hovered ? '#ef4444' : 'rgba(239,68,68,0.4)'
    if (isHotfix)  return hovered ? '#dc2626' : '#fca5a5'
    if (isCCR)     return hovered ? '#8b5cf6' : '#c4b5fd'
    if (isOrange)  return hovered ? '#ea580c' : '#fb923c'
    return hovered ? 'var(--accent)' : 'var(--border)'
  }

  const getBoxShadow = (hovered = false) => {
    if (isHotfix) return hovered
      ? '0 0 0 2px rgba(239,68,68,0.2), 0 4px 16px rgba(239,68,68,0.12)'
      : '0 0 0 2px rgba(239,68,68,0.12), 0 1px 3px rgba(0,0,0,0.04)'
    if (isCCR) return hovered
      ? '0 4px 20px rgba(139,92,246,0.20)'
      : '0 2px 12px rgba(139,92,246,0.12), inset 0 0 0 1px rgba(139,92,246,0.06)'
    return hovered
      ? '0 4px 16px rgba(91,79,255,0.1)'
      : '0 1px 3px rgba(0,0,0,0.04)'
  }

  const titleColor = isCCR ? '#4c1d95' : isLocked ? '#9ca3af' : 'var(--ink)'
  const bodyColor  = isCCR ? '#6d28d9' : isLocked ? '#d1d5db' : 'var(--ink-muted)'

  return (
    <div
      onClick={handleClick}
      className="rounded-2xl cursor-pointer transition-all duration-200"
      style={{
        position:  'relative',
        background: getBackground(),
        border:    `1.5px solid ${getBorderColor()}`,
        boxShadow:  getBoxShadow(),
        cursor:     isLocked ? 'not-allowed' : 'pointer',
        opacity:    isLocked ? 0.72 : 1,
        overflow:   'hidden',
        minWidth:   0,
        width:      '100%',
        boxSizing:  'border-box',
        userSelect: 'none',
      }}
      onMouseEnter={e => {
        if (isLocked) return
        e.currentTarget.style.borderColor = getBorderColor(true)
        e.currentTarget.style.boxShadow   = getBoxShadow(true)
        e.currentTarget.style.transform   = 'translateY(-2px)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = getBorderColor(false)
        e.currentTarget.style.boxShadow   = getBoxShadow(false)
        e.currentTarget.style.transform   = 'translateY(0)'
      }}
    >
      {/* ── Lock overlay — production incident (v2) ───────────────────────── */}
      {isLocked && (
        <div
          style={{
            position:       'absolute',
            inset:          0,
            borderRadius:   'inherit',
            background:     'rgba(239,68,68,0.04)',
            border:         '1.5px dashed rgba(239,68,68,0.35)',
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'center',
            zIndex:         10,
          }}
          onClick={e => e.stopPropagation()}
          onMouseDown={e => e.stopPropagation()}
        >
          <span style={{
            fontSize:    11,
            fontWeight:  700,
            color:       '#dc2626',
            background:  'rgba(255,255,255,0.92)',
            padding:     '3px 9px',
            borderRadius: 6,
            border:      '1px solid rgba(239,68,68,0.2)',
            pointerEvents: 'none',
          }}>
            🔒 Paused — SEV-1 Active
          </span>
        </div>
      )}

      {/* ── CCR left accent strip (v1) ────────────────────────────────────── */}
      {isCCR && (
        <div style={{
          position:     'absolute',
          left: 0, top: 0, bottom: 0,
          width:        4,
          background:   'linear-gradient(180deg, #8b5cf6, #7c3aed)',
          borderRadius: '16px 0 0 16px',
        }} />
      )}

      {/* ── CCR header strip (v1) ─────────────────────────────────────────── */}
      {isCCR && (
        <div style={{
          display:      'flex',
          alignItems:   'center',
          gap:          6,
          padding:      '6px 12px 6px 16px',
          background:   'linear-gradient(90deg, #ede9fe 0%, #f5f3ff 100%)',
          borderBottom: '1px solid #ddd6fe',
          marginLeft:   4,
          overflow:     'hidden',
        }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#7c3aed" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>
          </svg>
          <span style={{
            fontSize:      9,
            fontWeight:    800,
            color:         '#6d28d9',
            textTransform: 'uppercase',
            letterSpacing: '0.07em',
            overflow:      'hidden',
            textOverflow:  'ellipsis',
            whiteSpace:    'nowrap',
          }}>Client Change Request</span>
        </div>
      )}

      {/* ── Card body ─────────────────────────────────────────────────────── */}
      <div style={{
        padding:     '12px',
        paddingLeft: isCCR ? 16 : 12,
        overflow:    'hidden',
        minWidth:    0,
      }}>

        {/* Title row */}
        <div style={{
          display:        'flex',
          alignItems:     'flex-start',
          justifyContent: 'space-between',
          gap:            8,
          marginBottom:   8,
          minWidth:       0,
        }}>
          <div className="flex flex-col gap-1" style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
            {/* Hotfix badge above title (v2) */}
            {isHotfix && (
              <span style={{
                display:       'inline-flex',
                alignItems:    'center',
                gap:           4,
                fontSize:      9,
                fontWeight:    700,
                color:         '#dc2626',
                background:    '#fef2f2',
                border:        '1px solid #fecaca',
                padding:       '1px 6px',
                borderRadius:  4,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                width:         'fit-content',
              }}>
                🚨 HOTFIX — SEV-1
              </span>
            )}
            <h3
              className="font-semibold text-sm leading-snug line-clamp-2"
              style={{ color: titleColor, fontFamily: 'Syne, sans-serif' }}
            >
              {/* Strip [HOTFIX] prefix — badge above already shows it (v2) */}
              {isHotfix ? task.title.replace('[HOTFIX] ', '') : task.title}
            </h3>
          </div>

          {/* Status + mid-sprint tag */}
          <div className="flex items-center gap-1" style={{ flexShrink: 0 }}>
            {task.mid_sprint_changed && !isCCR && !isHotfix && (
              <MidSprintChangeTag />
            )}
            <StatusBadge status={task.status} />
          </div>
        </div>

        {/* Description */}
        <p className="text-xs line-clamp-2" style={{ color: bodyColor, marginBottom: 12, overflow: 'hidden' }}>
          {task.description}
        </p>

        {/* Footer row */}
        <div style={{
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'space-between',
          gap:            8,
          minWidth:       0,
          overflow:       'hidden',
        }}>
          {/* Left: priority + role pill */}
          <div style={{
            display:    'flex',
            alignItems: 'center',
            gap:        6,
            minWidth:   0,
            overflow:   'hidden',
            flexShrink: 1,
          }}>
            <div style={{ flexShrink: 0 }}>
              <PriorityBadge priority={task.priority} />
            </div>
            <span
              className="text-xs capitalize font-medium"
              style={{
                background:   isCCR ? '#ede9fe' : 'var(--surface-2)',
                color:        isCCR ? '#7c3aed' : bodyColor,
                border:       isCCR ? '1px solid #ddd6fe' : '1px solid var(--border)',
                padding:      '2px 8px',
                borderRadius: 6,
                overflow:     'hidden',
                textOverflow: 'ellipsis',
                whiteSpace:   'nowrap',
                flexShrink:   1,
                minWidth:     0,
              }}
            >
              {task.intern_role}
            </span>
          </div>

          {/* Right: score + due date + PR tick */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            {task.score != null && (
              <span className="text-xs font-semibold" style={{ color: isCCR ? '#7c3aed' : 'var(--accent)' }}>
                {task.score}/100
              </span>
            )}
            {task.due_date && (
              <span className="text-xs" style={{
                color:      isOverdue ? 'var(--red)' : (isCCR ? '#8b5cf6' : 'var(--ink-muted)'),
                whiteSpace: 'nowrap',
              }}>
                {isOverdue ? '⚠ ' : ''}
                {new Date(task.due_date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}
              </span>
            )}
            {/* PR tick on hotfix cards (v2) */}
            {isHotfix && task.github_pr_url && (
              <span style={{ fontSize: 11, color: '#16a34a', fontWeight: 700 }}>✓ PR</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}