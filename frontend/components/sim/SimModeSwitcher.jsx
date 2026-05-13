'use client'

/**
 * SimModeSwitcher.jsx
 * Place at: src/components/sim/SimModeSwitcher.jsx
 *
 * Dropdown for the top navbar (right of NotificationBell).
 * Implemented modes: client_change_request, qa_bug_flood, production_incident, teammate_quiet
 */

import { useRef, useState, useEffect } from 'react'
import { useSimMode, SIM_MODES } from '@/lib/store/simModeStore'

// Merged from all three independent implementations
const IMPLEMENTED = new Set([
  null,
  'client_change_request', // v1
  'qa_bug_flood',          // v1
  'production_incident',   // v2
  'teammate_quiet',        // v3
])

export default function SimModeSwitcher() {
  const { activeMode, activeModeData, setActiveMode } = useSimMode()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    function outside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    if (open) document.addEventListener('mousedown', outside)
    return () => document.removeEventListener('mousedown', outside)
  }, [open])

  const isActive = activeMode !== null

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <style>{`
        .sms-btn {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 5px 10px 5px 8px;
          border-radius: 10px;
          border: 1.5px solid var(--border, #e5e7eb);
          background: var(--surface, #fff);
          cursor: pointer;
          font-size: 12px;
          font-weight: 600;
          color: var(--ink-soft, #6b7280);
          transition: border-color 0.15s, background 0.15s;
          white-space: nowrap;
          line-height: 1;
          font-family: inherit;
        }
        .sms-btn:hover {
          border-color: var(--accent, #5b4fff);
          background: var(--accent-soft, #ede9ff);
          color: var(--accent, #5b4fff);
        }
        .sms-btn.sms-active {
          border-color: var(--_sms-c);
          background: var(--_sms-bg);
          color: var(--_sms-c);
        }
        .sms-dot {
          width: 7px; height: 7px;
          border-radius: 50%; flex-shrink: 0;
        }
        .sms-dot-pulse { animation: sms-pulse 1.8s ease-in-out infinite; }
        @keyframes sms-pulse {
          0%,100% { opacity:1; transform:scale(1); }
          50% { opacity:.55; transform:scale(1.4); }
        }
        .sms-chevron {
          width: 11px; height: 11px;
          transition: transform 0.15s;
          opacity: 0.55; flex-shrink: 0;
        }
        .sms-chevron-open { transform: rotate(180deg); }

        /* Popover */
        .sms-popover {
          position: absolute;
          right: 0;
          top: calc(100% + 8px);
          width: 250px;
          border-radius: 14px;
          background: rgba(255,255,255,0.98);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border: 1px solid rgba(91,79,255,0.12);
          box-shadow: 0 8px 40px rgba(0,0,0,0.1), 0 2px 10px rgba(0,0,0,0.06);
          overflow: hidden;
          z-index: 200;
          animation: sms-down 0.16s cubic-bezier(0.34,1.4,0.64,1);
        }
        @keyframes sms-down {
          from { opacity:0; transform:translateY(-6px) scale(0.97); }
          to   { opacity:1; transform:translateY(0) scale(1); }
        }
        .sms-pop-head {
          padding: 9px 13px 8px;
          font-size: 10px; font-weight: 700;
          letter-spacing: 0.08em; text-transform: uppercase;
          color: var(--ink-muted, #9ca3af);
          border-bottom: 1px solid var(--border, #f0f0f0);
        }
        .sms-row {
          display: flex; align-items: center; gap: 9px;
          padding: 8px 13px;
          cursor: pointer;
          background: transparent;
          border: none; width: 100%; text-align: left;
          transition: background 0.1s;
          font-family: inherit;
        }
        .sms-row:hover { background: var(--surface-2, #f9fafb); }
        .sms-row-sel { background: var(--accent-soft, #ede9ff) !important; }
        .sms-row-icon { font-size: 14px; flex-shrink: 0; line-height: 1; }
        .sms-row-info { flex: 1; min-width: 0; }
        .sms-row-name {
          font-size: 12px; font-weight: 600;
          color: var(--ink, #111);
          display: flex; align-items: center; gap: 5px;
        }
        .sms-row-desc {
          font-size: 11px; color: var(--ink-muted, #9ca3af);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          margin-top: 1px;
        }
        .sms-badge-pill {
          font-size: 9px; font-weight: 800;
          padding: 1px 5px; border-radius: 4px;
          letter-spacing: 0.04em;
        }
        .sms-soon {
          font-size: 9px; font-weight: 600;
          padding: 1px 5px; border-radius: 4px;
          background: var(--surface-2, #f3f4f6);
          color: var(--ink-muted, #9ca3af);
          margin-left: auto; flex-shrink: 0;
        }
        .sms-check {
          width: 13px; height: 13px;
          color: var(--accent, #5b4fff); flex-shrink: 0;
        }
      `}</style>

      {/* Trigger */}
      <button
        className={`sms-btn${isActive ? ' sms-active' : ''}`}
        style={isActive ? {
          '--_sms-c': activeModeData.color,
          '--_sms-bg': activeModeData.bg,
        } : {}}
        onClick={() => setOpen(o => !o)}
        title="Simulation Mode"
      >
        <span
          className={`sms-dot${isActive ? ' sms-dot-pulse' : ''}`}
          style={{ background: activeModeData.color }}
        />
        {isActive ? (activeModeData.badge ?? activeModeData.label.split(' ')[0]) : 'Sim Mode'}
        <svg className={`sms-chevron${open ? ' sms-chevron-open' : ''}`} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M2 4l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="sms-popover">
          <div className="sms-pop-head">Simulation Mode</div>

          {SIM_MODES.map(mode => {
            const isSelected = activeMode === mode.id
            const isImpl = IMPLEMENTED.has(mode.id)

            return (
              <button
                key={mode.id ?? 'normal'}
                className={`sms-row${isSelected ? ' sms-row-sel' : ''}`}
                onClick={() => {
                  if (!isImpl) return // block "soon" modes
                  setActiveMode(mode.id)
                  setOpen(false)
                }}
                style={{ cursor: isImpl ? 'pointer' : 'default' }}
              >
                <span className="sms-row-icon">{mode.icon}</span>
                <span className="sms-row-info">
                  <span className="sms-row-name">
                    {mode.label}
                    {mode.badge && (
                      <span
                        className="sms-badge-pill"
                        style={{ background: mode.bg, color: mode.color, border: `1px solid ${mode.border}` }}
                      >
                        {mode.badge}
                      </span>
                    )}
                  </span>
                  <span className="sms-row-desc">{mode.description}</span>
                </span>

                {isSelected ? (
                  <svg className="sms-check" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2.2">
                    <path d="M2 7l3.5 3.5L12 4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : !isImpl ? (
                  <span className="sms-soon">Soon</span>
                ) : null}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}