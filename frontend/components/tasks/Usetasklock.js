// frontend/components/tasks/useTaskLock.js
import { useEffect, useState } from 'react'
import { useIncidentState } from '@/components/sim/ProductionIncidentPanel'

/**
 * useTaskLock(task)
 * Returns lock state for a task card during an active SEV-1 incident.
 *
 * isLocked  → true for paused sprint tasks (block click/drag)
 * isHotfix  → true for [HOTFIX] tasks (let them flow normally)
 */
export function useTaskLock(task) {
  const { active, hotfixTaskIds } = useIncidentState()

  if (!active) {
    return { isLocked: false, isHotfix: false, incidentActive: false }
  }

  const isHotfix =
    task?.title?.startsWith('[HOTFIX]') || hotfixTaskIds.includes(task?.id)

  // Only paused sprint tasks are locked — hotfix tasks are always interactive
  const isLocked = !isHotfix && task?.status === 'paused'

  return { isLocked, isHotfix, incidentActive: true }
}