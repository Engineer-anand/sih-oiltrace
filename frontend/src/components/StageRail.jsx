const stages = [
  ['input', 'Input & georeferencing'],
  ['detection', 'U-Net detection'],
  ['geospatial', 'Geospatial reconstruction'],
  ['drift', 'Backward drift & origin'],
  ['ais', 'AIS candidate matching'],
  ['scoring', 'Attribution scoring'],
  ['evidence', 'Evidence & report'],
  ['save', 'Save investigation'],
]

// `statuses` (optional): real backend job.stages map of { [id]: {status, detail} }
// pushed live from GET/SSE /pipeline/jobs/{id}. Falls back to the legacy
// single active/error id when a job isn't in flight (e.g. idle state).
export default function StageRail({ active, error, statuses }) {
  return <div className="stage-rail">{stages.map(([id, label], i) => {
    const live = statuses?.[id]?.status
    const detail = statuses?.[id]?.detail || ''
    const skipped = live === 'done' && detail.startsWith('skipped')
    const cls = skipped ? 'skipped' : live ? live : (error === id ? 'error' : (active === id ? 'active' : ''))
    const mark = cls === 'error' ? '!' : cls === 'done' ? '✓' : cls === 'skipped' ? '–' : i + 1
    const duration = statuses?.[id]?.duration_seconds
    return <div key={id} className={`stage ${cls}`} title={detail}>
      <span>{mark}</span><b>{label}</b>{duration != null && <small>{duration.toFixed(2)}s</small>}
    </div>
  })}</div>
}
