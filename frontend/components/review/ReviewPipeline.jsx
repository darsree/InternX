'use client'
/**
 * frontend/components/review/ReviewPipeline.jsx
 *
 * Phase 4 — Progressive 3-layer status panel.
 * Shows each layer's result as it completes — not a single loading spinner.
 *
 * Props:
 *   layer1   { verdict, ci, security, error_logs } | null
 *   layer2   { risk_score, risk_label, pr_type, complexity, display } | null
 *   layer3   { verdict, score, review_summary, skip_if_passing } | null
 *   status   "pending" | "layer1_complete" | "layer2_complete" | "complete" | "blocked"
 *   loading  bool — overall pipeline still in progress
 */

// ── Status helpers ─────────────────────────────────────────────────────────

const STATUS = {
  waiting:  { dot: '#94a3b8', label: 'Waiting',  pulse: false },
  pending:  { dot: '#f59e0b', label: 'Running…', pulse: true  },
  pass:     { dot: '#22c55e', label: 'Passed',   pulse: false },
  block:    { dot: '#ef4444', label: 'Blocked',  pulse: false },
  skip:     { dot: '#a78bfa', label: 'Skipped (low risk)', pulse: false },
  complete: { dot: '#22c55e', label: 'Complete', pulse: false },
  resubmit: { dot: '#f97316', label: 'Needs work', pulse: false },
}

function StatusDot({ type }) {
  const s = STATUS[type] || STATUS.waiting
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${s.pulse ? 'animate-pulse' : ''}`}
      style={{ background: s.dot }}
      title={s.label}
    />
  )
}

// ── Layer 1 detail panel ────────────────────────────────────────────────────

function Layer1Detail({ layer1 }) {
  if (!layer1) return null
  const { ci, error_logs } = layer1

  if (layer1.verdict === 'pass') {
    return (
      <div className="mt-2 space-y-1.5">
        {ci?.checks_run > 0 && (
          <p className="text-xs" style={{ color: 'var(--ink-muted)' }}>
            ✅ {ci.checks_run} CI check{ci.checks_run !== 1 ? 's' : ''} passed
          </p>
        )}
        {(!ci || ci.checks_run === 0) && (
          <p className="text-xs" style={{ color: 'var(--ink-muted)' }}>
            ✅ Diff fetched · No secrets detected · Sanity check passed
          </p>
        )}
      </div>
    )
  }

  // Blocked
  return (
    <div className="mt-2 space-y-1.5">
      {(error_logs || []).map((log, i) => (
        <p key={i} className="text-xs font-medium px-2 py-1 rounded-lg"
          style={{ background: '#fef2f2', color: '#dc2626', border: '1px solid #fecaca' }}>
          {log}
        </p>
      ))}
      {ci?.failed_checks?.length > 0 && (
        <div className="text-xs space-y-1">
          {ci.failed_checks.map((fc, i) => (
            <div key={i} className="flex items-center gap-1.5 flex-wrap">
              <span style={{ color: '#dc2626' }}>•</span>
              {fc.url ? (
                <a href={fc.url} target="_blank" rel="noopener noreferrer"
                  className="underline" style={{ color: '#dc2626' }}>
                  {fc.name}
                </a>
              ) : (
                <span style={{ color: '#dc2626' }}>{fc.name}</span>
              )}
              <span style={{ color: '#9f1239' }}>({fc.conclusion})</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Layer 2 detail panel ────────────────────────────────────────────────────

function Layer2Detail({ layer2 }) {
  if (!layer2) return null
  const d = layer2.display || {}
  const riskColor =
    layer2.risk_label === 'high'   ? '#ef4444' :
    layer2.risk_label === 'medium' ? '#f59e0b' : '#22c55e'

  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {[d.badge, d.pr_type, d.complexity].filter(Boolean).map((label, i) => (
        <span key={i} className="text-[11px] px-2.5 py-0.5 rounded-full font-medium"
          style={{
            background: i === 0 ? `${riskColor}18` : 'var(--surface-2)',
            color: i === 0 ? riskColor : 'var(--ink-muted)',
            border: `1px solid ${i === 0 ? `${riskColor}40` : 'var(--border)'}`,
          }}>
          {label}
        </span>
      ))}
    </div>
  )
}

// ── Layer 3 detail panel ────────────────────────────────────────────────────

function Layer3Detail({ layer3 }) {
  if (!layer3) return null

  if (layer3.skip_if_passing) {
    return (
      <div className="mt-2 px-3 py-2 rounded-lg text-xs"
        style={{ background: '#f5f3ff', color: '#7c3aed', border: '1px solid #e9d5ff' }}>
        ⚡ Low-risk PR — AI mentor review skipped (saves tokens + latency)
      </div>
    )
  }

  return (
    <div className="mt-2 space-y-1.5">
      {layer3.score != null && (
        <p className="text-xs font-semibold" style={{
          color: layer3.verdict === 'pass' ? '#16a34a' : '#ea580c'
        }}>
          Score: {layer3.score}/100
        </p>
      )}
      {layer3.review_summary && (
        <p className="text-xs line-clamp-2" style={{ color: 'var(--ink-muted)' }}>
          {layer3.review_summary}
        </p>
      )}
    </div>
  )
}

// ── Single layer row ────────────────────────────────────────────────────────

function LayerRow({ num, label, sublabel, statusType, children, isLast }) {
  const s = STATUS[statusType] || STATUS.waiting
  return (
    <div className="flex gap-3">
      {/* Left: number + connector line */}
      <div className="flex flex-col items-center flex-shrink-0" style={{ width: 28 }}>
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
          style={{
            background: statusType === 'pass' || statusType === 'complete'
              ? '#dcfce7'
              : statusType === 'block' || statusType === 'resubmit'
                ? '#fee2e2'
                : statusType === 'pending'
                  ? '#fef3c7'
                  : statusType === 'skip'
                    ? '#ede9fe'
                    : 'var(--surface-2)',
            color: statusType === 'pass' || statusType === 'complete'
              ? '#166534'
              : statusType === 'block' || statusType === 'resubmit'
                ? '#dc2626'
                : statusType === 'pending'
                  ? '#92400e'
                  : statusType === 'skip'
                    ? '#7c3aed'
                    : 'var(--ink-muted)',
            border: '2px solid var(--border)',
          }}
        >
          {statusType === 'pass' || statusType === 'complete' ? '✓'
            : statusType === 'block'   ? '✕'
            : statusType === 'skip'    ? '⚡'
            : num}
        </div>
        {!isLast && (
          <div className="flex-1 w-0.5 my-1" style={{ background: 'var(--border)', minHeight: 16 }} />
        )}
      </div>

      {/* Right: content */}
      <div className="flex-1 pb-4 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold" style={{ color: 'var(--ink)' }}>{label}</p>
          <StatusDot type={statusType} />
          <span className="text-[11px]" style={{ color: 'var(--ink-muted)' }}>{s.label}</span>
        </div>
        <p className="text-xs mt-0.5" style={{ color: 'var(--ink-muted)' }}>{sublabel}</p>
        {children}
      </div>
    </div>
  )
}

// ── Main component ──────────────────────────────────────────────────────────

export function ReviewPipeline({ layer1, layer2, layer3, status, loading }) {
  // Derive per-layer status
  const l1Status =
    !layer1                           ? (loading ? 'pending' : 'waiting')
    : layer1.verdict === 'pass'       ? 'pass'
    : layer1.verdict === 'block'      ? 'block'
    : 'pending'

  const l2Status =
    l1Status === 'block'              ? 'waiting'
    : !layer2 && loading              ? (status === 'layer1_complete' ? 'pending' : 'waiting')
    : !layer2                         ? 'waiting'
    : 'pass'

  const l3SkipPassing = layer3?.skip_if_passing
  const l3Status =
    l2Status === 'waiting'            ? 'waiting'
    : l3SkipPassing                   ? 'skip'
    : !layer3 && loading              ? (status === 'layer2_complete' ? 'pending' : 'waiting')
    : !layer3                         ? 'waiting'
    : layer3.verdict === 'pass'       ? 'pass'
    : layer3.verdict === 'resubmit'   ? 'resubmit'
    : layer3.verdict                  ? 'complete'
    : 'pending'

  return (
    <div
      className="rounded-2xl px-5 py-4"
      style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm font-bold" style={{ color: 'var(--ink)' }}>
          🔬 3-Layer Review Pipeline
        </p>
        {loading && (
          <span className="text-[11px] px-2 py-0.5 rounded-full animate-pulse"
            style={{ background: '#e0e7ff', color: '#3730a3' }}>
            Running…
          </span>
        )}
      </div>

      <LayerRow
        num={1}
        label="Deterministic Gate"
        sublabel="CI checks · Secret scan · Diff sanity"
        statusType={l1Status}
      >
        <Layer1Detail layer1={layer1} />
      </LayerRow>

      <LayerRow
        num={2}
        label="ML Risk Scorer"
        sublabel="CodeBERT embeddings · PR type · Risk score"
        statusType={l2Status}
      >
        <Layer2Detail layer2={layer2} />
      </LayerRow>

      <LayerRow
        num={3}
        label="AI Mentor"
        sublabel="Groq · Non-blocking guidance · Risk-context injected"
        statusType={l3Status}
        isLast
      >
        <Layer3Detail layer3={layer3} />
      </LayerRow>

      {/* Blocked hard-gate banner */}
      {l1Status === 'block' && (
        <div className="mt-3 px-4 py-3 rounded-xl"
          style={{ background: '#fef2f2', border: '1px solid #fecaca' }}>
          <p className="text-sm font-bold" style={{ color: '#dc2626' }}>
            🔴 PR Blocked — Layer 1 Gate Failed
          </p>
          <p className="text-xs mt-1" style={{ color: '#7f1d1d' }}>
            Fix the issues above before AI review runs. This is a hard gate — no AI tokens were consumed.
          </p>
        </div>
      )}
    </div>
  )
}