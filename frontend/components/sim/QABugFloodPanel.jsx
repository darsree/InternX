'use client'

/**
 * QABugFloodPanel.jsx
 * Place at: src/components/sim/QABugFloodPanel.jsx
 *
 * Reads QBF state from SimModeContext (singleton) — no separate hook call.
 */

import { useEffect, useState } from 'react'
import { useSimMode } from '@/lib/store/simModeStore'

const STYLES = `
  .qbf-strip {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 20px;
    height: 44px;
    background: #f0fdf4;
    border-bottom: 1.5px solid #bbf7d0;
    font-size: 12px;
    font-family: inherit;
  }
  .qbf-badge {
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.07em;
    padding: 2px 7px;
    border-radius: 5px;
    background: rgba(22,163,74,0.10);
    color: #16a34a;
    border: 1px solid rgba(22,163,74,0.28);
    flex-shrink: 0;
  }
  .qbf-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #16a34a;
    flex-shrink: 0;
  }
  .qbf-dot.live { animation: qbf-blink 1.4s ease-in-out infinite; }
  @keyframes qbf-blink {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.25; }
  }
  .qbf-label { color: #166534; font-weight: 500; flex: 1; }
  .qbf-count {
    font-size: 10px; font-weight: 700;
    background: #16a34a; color: #fff;
    border-radius: 20px; padding: 1px 8px;
  }
  .qbf-err { color: #dc2626; font-size: 11px; font-weight: 500; }
  .qbf-progress-track {
    flex: 1; height: 4px; border-radius: 2px;
    background: #bbf7d0; overflow: hidden; max-width: 120px;
  }
  .qbf-progress-fill {
    height: 100%; background: #16a34a;
    border-radius: 2px; transition: width 0.3s ease;
  }
  .qbf-end-btn {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px; border-radius: 6px;
    font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
    cursor: pointer; background: transparent; color: #166534;
    border: 1.5px solid #16a34a66;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
    flex-shrink: 0; font-family: inherit;
  }
  .qbf-end-btn:hover { background: #dc262615; color: #dc2626; border-color: #dc262655; }
`

export default function QABugFloodPanel() {
  const { qbfPhase, generatedTickets: qbfTickets = [], qbfError, endQbfMode } = useSimMode()

  // Hydration fix — skip SSR render
  const [mounted, setMounted] = useState(false)
  useEffect(() => { setMounted(true) }, [])
  if (!mounted) return null

  const progressPct = Math.round((qbfTickets.length / 6) * 100)

  return (
    <>
      {/* FIX: dangerouslySetInnerHTML was split across two lines (syntax error) — merged onto one line */}
      <style dangerouslySetInnerHTML={{ __html: STYLES }} />
      <div className="qbf-strip">
        <span className="qbf-badge">QBF</span>
        <div className={`qbf-dot${qbfPhase === 'inserting' ? ' live' : ''}`} />

        <span className="qbf-label">
          {qbfPhase === 'inserting' && `Filing QA bugs… (${qbfTickets.length}/6)`}
          {qbfPhase === 'done'      && `${qbfTickets.length} QA bugs filed — visible on the ticket board.`}
          {qbfPhase === 'idle'      && 'QA Bug Flood active — preparing tickets…'}
          {qbfPhase === 'error'     && <span className="qbf-err">{qbfError ?? 'Something went wrong.'}</span>}
        </span>

        {qbfPhase === 'inserting' && (
          <div className="qbf-progress-track">
            <div className="qbf-progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
        )}

        {qbfTickets.length > 0 && (
          <span className="qbf-count">{qbfTickets.length}</span>
        )}

        {(qbfPhase === 'done' || qbfPhase === 'error') && endQbfMode && (
          <button className="qbf-end-btn" onClick={endQbfMode}>
            ✕ End Simulation
          </button>
        )}
      </div>
    </>
  )
}