'use client'

/**
 * simModeStore.jsx
 * Place at: src/lib/store/simModeStore.jsx
 *
 * React context for Simulation Mode. Persists to sessionStorage.
 *
 * FIX: calls useQABugFlood(activeMode) inside the Provider and exposes
 * qbfPhase, generatedTickets, qbfError, and endQbfMode through context
 * so QABugFloodPanel can read them via useSimMode().
 */

import { createContext, useContext, useState, useEffect } from 'react'
import { useQABugFlood } from '@/lib/store/useQABugFlood'

export const SIM_MODES = [
  {
    id: null,
    label: 'Normal Mode',
    description: 'Standard internship view',
    color: '#22c55e',
    bg: '#f0fdf4',
    border: '#bbf7d0',
    badge: null,
  },
  {
    id: 'production_incident',
    label: 'Production Incident',
    badge: 'SEV-1',
    description: 'Critical system failure simulation',
    color: '#ef4444',
    bg: '#fff1f2',
    border: '#fecaca',
  },
  {
    id: 'client_change_request',
    label: 'Client Change Request',
    badge: 'CCR',
    description: 'Mid-sprint requirement changes',
    color: '#f59e0b',
    bg: '#fffbeb',
    border: '#fde68a',
  },
  {
    id: 'teammate_quiet',
    label: 'Teammate Goes Quiet',
    badge: 'TGQ',
    description: 'Absent team member simulation',
    color: '#3b82f6',
    bg: '#eff6ff',
    border: '#bfdbfe',
  },
  {
    id: 'qa_bug_flood',
    label: 'QA Bug Flood',
    badge: 'QBF',
    description: 'Mass bug report simulation',
    color: '#f97316',
    bg: '#fff7ed',
    border: '#fed7aa',
  },
]

const SimModeContext = createContext(null)

export function SimModeProvider({ children }) {
  const [activeMode, setActiveModeState] = useState(null)

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem('internx_sim_mode')
      if (saved) setActiveModeState(saved)
    } catch {}
  }, [])

  const setActiveMode = (modeId) => {
    setActiveModeState(modeId)
    try {
      if (modeId) sessionStorage.setItem('internx_sim_mode', modeId)
      else sessionStorage.removeItem('internx_sim_mode')
    } catch {}
  }

  const activeModeData = SIM_MODES.find(m => m.id === activeMode) ?? SIM_MODES[0]

  // Convenience: deactivateMode() resets to normal mode (null)
  const deactivateMode = () => setActiveMode(null)

  // FIX: call the QBF hook here, passing activeMode as a parameter.
  // This is the single place the hook runs — QABugFloodPanel reads the
  // resulting state from context instead of calling the hook itself.
  const {
    phase: qbfPhase,
    generatedTickets,
    error: qbfError,
    resetFlood,
  } = useQABugFlood(activeMode)

  // endQbfMode: auto-resolves inserted tickets/tasks then returns to Normal Mode
  const endQbfMode = async () => {
    await resetFlood()
    deactivateMode()
  }

  return (
    <SimModeContext.Provider value={{
      activeMode,
      activeModeData,
      setActiveMode,
      deactivateMode,
      SIM_MODES,
      // QBF state exposed for QABugFloodPanel
      qbfPhase,
      generatedTickets,
      qbfError,
      endQbfMode,
    }}>
      {children}
    </SimModeContext.Provider>
  )
}

export function useSimMode() {
  const ctx = useContext(SimModeContext)
  if (!ctx) throw new Error('useSimMode must be used inside SimModeProvider')
  return ctx
}