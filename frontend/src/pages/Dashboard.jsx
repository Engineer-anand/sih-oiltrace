import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_BASE, apiJson, downloadFile } from '../lib/api.js'
import MapView from '../components/MapView.jsx'
import DriftAnimator from '../components/DriftAnimator.jsx'
import Timeline from '../components/Timeline.jsx'
import Metric from '../components/Metric.jsx'
import StageRail from '../components/StageRail.jsx'
import StatusPill from '../components/StatusPill.jsx'
import { formatMmsi, formatVesselName } from '../lib/vesselFormat.js'

function toIsoSlice(d) { return d.toISOString().slice(0, 16) }
function getDefaultDates() {
  const now = new Date()
  const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 3600 * 1000)
  return { start: toIsoSlice(sevenDaysAgo), end: toIsoSlice(now), detectedAt: toIsoSlice(now) }
}
function formatDisplayDate(isoStr) {
  if (!isoStr) return '—'
  const d = new Date(isoStr)
  if (isNaN(d.getTime())) return isoStr
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) + ', ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false })
}

const REGION_PRESETS = [
  { name: 'Gulf of Mexico', aoi: [[-90.5, 27.5], [-87.5, 29.5]] },
  { name: 'North Sea', aoi: [[1.5, 55.5], [4.5, 57.5]] },
  { name: 'Indian Ocean', aoi: [[96.5, 4.5], [99.5, 6.5]] },
]
const AOI_STORAGE_KEY = 'oiltrace_aoi_v1'
function loadSavedAoi() {
  try {
    const raw = localStorage.getItem(AOI_STORAGE_KEY)
    if (raw === 'null') return null
    if (raw) { const parsed = JSON.parse(raw); if (Array.isArray(parsed) && parsed.length === 2) return parsed }
  } catch { }
  return null
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [datePreset, setDatePreset] = useState('7d')
  const [form, setForm] = useState(() => {
    const dates = getDefaultDates()
    return { source: 'aws', polarization: 'vv', aoi: loadSavedAoi(), start: dates.start, end: dates.end, detectedAt: dates.detectedAt, lookback: 6, radius: 20, aisSource: 'scenario', file: null, scene: null }
  })
  const [scenes, setScenes] = useState([])
  const [data, setData] = useState(null)
  const [history, setHistory] = useState([])
  const [tab, setTab] = useState('overview')
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState('input')
  const [error, setError] = useState('')
  const [health, setHealth] = useState(null)
  const [localBounds, setLocalBounds] = useState(['', '', '', ''])
  const [events, setEvents] = useState([])
  const [elapsed, setElapsed] = useState(0)
  const [stageStatuses, setStageStatuses] = useState({})
  const [selectedVesselId, setSelectedVesselId] = useState(null)
  const lastSpillIdRef = useRef(null)
  const jobCancelRef = useRef(null) // active jobId being tracked; cleared on unmount

  useEffect(() => () => {
    // M12: cleanup on unmount — close the SSE stream and stop polling.
    jobCancelRef.current = null
    try { jobCancelRef.stream?.close() } catch { }
  }, [])

  useEffect(() => {
    const currentSpillId = data?.spill_id || (data?.vessels?.length ? (data.vessels[0]?.vessel_id || 'incident') : null)
    if (data?.vessels?.length && currentSpillId !== lastSpillIdRef.current) {
      lastSpillIdRef.current = currentSpillId
      setSelectedVesselId(data.vessels[0].vessel_id)
    } else if (!data?.vessels?.length) { lastSpillIdRef.current = null; setSelectedVesselId(null) }
  }, [data])

  const update = useCallback((k, v) => {
    if (k === 'aoi') { try { localStorage.setItem(AOI_STORAGE_KEY, v ? JSON.stringify(v) : 'null') } catch { } }
    setForm(f => ({ ...f, [k]: v }))
  }, [])
  const handleAoiChange = useCallback((value) => update('aoi', value), [update])
  const bbox = useMemo(() => form.aoi ? [Math.min(form.aoi[0][0], form.aoi[1][0]), Math.min(form.aoi[0][1], form.aoi[1][1]), Math.max(form.aoi[0][0], form.aoi[1][0]), Math.max(form.aoi[0][1], form.aoi[1][1])] : null, [form.aoi])

  const applyDatePreset = useCallback((days) => {
    const end = new Date(); const start = new Date(Date.now() - days * 24 * 3600 * 1000)
    update('start', toIsoSlice(start)); update('end', toIsoSlice(end)); setDatePreset(`${days}d`)
  }, [update])

  async function checkHealth() {
    try {
      const h = await apiJson('/health')
      setHealth(h)
      if (h?.app_mode && h.app_mode === 'production') {
        setForm((f) => ({ ...f, aisSource: f.aisSource || 'gfw' }))
      } else {
        setForm((f) => ({ ...f, aisSource: f.aisSource || 'scenario' }))
      }
    } catch (e) { setHealth({ status: 'offline', detail: e.message }) }
  }
  async function loadHistory() { try { setHistory(await apiJson('/api/v1/spills')) } catch { } }

  useEffect(() => {
    checkHealth(); loadHistory()
    document.body.classList.add('dash-body')
    return () => document.body.classList.remove('dash-body')
  }, [])

  function logEvent(message, kind = 'info') { setEvents(e => [{ time: new Date().toISOString(), message, kind }, ...e].slice(0, 30)) }

  async function searchScenes() {
    setError(''); setBusy(true); logEvent('Sentinel-1 archive search started')
    try {
      const q = new URLSearchParams({ polarization: form.polarization, limit: '8' })
      if (bbox) ['min_lon', 'min_lat', 'max_lon', 'max_lat'].forEach((k, i) => q.set(k, bbox[i]))
      if (form.start) q.set('start', new Date(form.start).toISOString())
      if (form.end) q.set('end', new Date(form.end).toISOString())
      const result = await apiJson('/api/v1/sentinel1/search?' + q)
      setScenes(result.scenes || [])
      logEvent(`Sentinel-1 search returned ${result.scenes?.length || 0} scene(s)`, result.scenes?.length ? 'success' : 'warn')
      if (!result.scenes?.length) setError('No public Sentinel-1 scenes matched this AOI/time window.')
    } catch (e) { setError(e.message); logEvent(`Sentinel-1 search error: ${e.message}`, 'error') } finally { setBusy(false) }
  }

  useEffect(() => {
    if (!busy) return
    const started = performance.now()
    const id = setInterval(() => setElapsed((performance.now() - started) / 1000), 100)
    return () => clearInterval(id)
  }, [busy])

  async function run() {
    if (jobCancelRef.current) {
      jobCancelRef.current = null
      try { jobCancelRef.stream?.close() } catch { }
      jobCancelRef.stream = null
    }
    setSelectedVesselId(null)
    lastSpillIdRef.current = null
    setElapsed(0); setBusy(true); setError(''); setStage('input'); setStageStatuses({}); setData(null)
    logEvent('OilTrace pipeline started')
    try {
      const fd = new FormData()
      if (form.file) fd.append('file', form.file)
      const activeBBox = form.source === 'aws'
        ? (form.scene?.bbox?.length === 4 ? form.scene.bbox : bbox)
        : (localBounds.every(v => v !== '') ? localBounds.map(Number) : null)
      if (activeBBox) ['min_lon', 'min_lat', 'max_lon', 'max_lat'].forEach((k, i) => fd.append(k, activeBBox[i]))
      if (form.detectedAt) fd.append('detected_at', new Date(form.detectedAt).toISOString())
      fd.append('lookback_hours', form.lookback); fd.append('ais_radius_km', form.radius); fd.append('ais_source', form.aisSource)
      if (form.scene) { fd.append('aws_scene_id', form.scene.id); fd.append('aws_polarization', form.polarization) }
      const { job_id } = await apiJson('/api/v1/pipeline/jobs', { method: 'POST', body: fd })
      logEvent(`Pipeline job queued (${job_id})`)
      await pollJob(job_id)
    } catch (e) { setError(e.message); logEvent(`Pipeline error: ${e.message}`, 'error'); setStage('input'); setBusy(false) }
  }

  async function pollJob(jobId) {
    const seen = new Set()
    jobCancelRef.current = jobId
    let isDone = false
    const alive = () => jobCancelRef.current === jobId && !isDone

    const applyJob = async (job) => {
      if (!job || !alive()) return isDone
      setStageStatuses(job.stages || {})
      if (job.stage) setStage(job.stage)
      Object.entries(job.stages || {}).forEach(([name, s]) => {
        const key = `${name}:${s.status}`
        if (s.status !== 'pending' && !seen.has(key)) {
          seen.add(key)
          if (s.status === 'active') logEvent(`Stage started: ${name}`)
          else if (s.status === 'done') logEvent(`Stage complete: ${name}${s.detail ? ' — ' + s.detail : ''}`, 'success')
          else if (s.status === 'degraded') logEvent(`Stage degraded: ${name} — ${s.detail || 'quality gate failed'}`, 'warn')
          else if (s.status === 'error') logEvent(`Stage failed: ${name} — ${s.detail || 'unknown error'}`, 'error')
        }
      })
      if (job.partial && Object.keys(job.partial).length) {
        setData(prev => ({
          ...(prev || {}),
          spill_id: job.partial.spill_id || prev?.spill_id,
          incident: job.partial.incident || prev?.incident,
          origin: job.partial.origin || prev?.origin,
          vessels: job.partial.vessels || prev?.vessels || [],
          evidence: prev?.evidence || [],
          message: 'Pipeline still running',
          using_fallback_ais: job.partial.using_fallback_ais ?? prev?.using_fallback_ais ?? false,
          ais_source: job.partial.ais_source || prev?.ais_source
        }))
      }
      if (job.status === 'done') {
        isDone = true
        setData(job.result)
        logEvent(job.result?.message || 'Pipeline completed', 'success')
        setTab('overview')
        await loadHistory()
        setBusy(false)
        return true
      }
      if (job.status === 'error') {
        isDone = true
        setError(job.error || 'Pipeline failed')
        logEvent(`Pipeline error: ${job.error}`, 'error')
        setBusy(false)
        return true
      }
      if (job.status === 'cancelled') {
        isDone = true
        setError('Job cancelled.')
        logEvent('Pipeline job cancelled', 'warn')
        setBusy(false)
        return true
      }
      return false
    }

    await new Promise(resolve => {
      let stream = null
      let fallbackStarted = false

      const endStream = () => {
        if (stream) {
          try { stream.close() } catch {}
          stream = null
        }
      }

      const handlePayload = async event => {
        try {
          const payload = JSON.parse(event.data)
          if (await applyJob(payload)) {
            endStream()
            resolve()
          }
        } catch {
          if (isDone || !alive()) {
            endStream()
            resolve()
          }
        }
      }

      try {
        stream = new EventSource(`${API_BASE}/api/v1/pipeline/jobs/${jobId}/events`)
        jobCancelRef.stream = stream
        stream.onmessage = handlePayload
        stream.addEventListener('terminal', handlePayload)
        stream.onerror = async () => {
          endStream()
          if (isDone || !alive()) { resolve(); return }
          if (fallbackStarted) return
          fallbackStarted = true

          let consecutiveErrors = 0
          for (let i = 0; i < 2250; i++) {
            if (isDone || !alive()) { resolve(); return }
            try {
              const job = await apiJson('/api/v1/pipeline/jobs/' + jobId)
              consecutiveErrors = 0
              if (await applyJob(job)) { resolve(); return }
            } catch (err) {
              consecutiveErrors++
              if (consecutiveErrors > 25) {
                if (alive() && !isDone) {
                  setError(err.message)
                  logEvent(`Job status error: ${err.message}`, 'error')
                  setBusy(false)
                }
                resolve()
                return
              }
            }
            await new Promise(r => setTimeout(r, 1000))
          }
          if (alive() && !isDone) {
            setError('Status polling timed out. Reload the investigation from history.')
            setBusy(false)
          }
          resolve()
        }
      } catch {
        // Fall back to polling immediately if EventSource fails
      }
    })

    if (jobCancelRef.current === jobId) jobCancelRef.current = null
  }

  async function loadIncident(id) {
    try {
      setBusy(true); setError('')
      const raw = await apiJson('/api/v1/spills/' + encodeURIComponent(id))
      if (raw && raw.spill) {
        // M17: derive source/mode from persisted backend truth — never hardcode.
        const persistedMode = raw.spill.data_mode || null
        const hasScenarioIds = (raw.vessels || []).some((v) => String(v.mmsi || '').startsWith('SCENARIO-'))
        const hasFallbackIds = (raw.vessels || []).some((v) => String(v.mmsi || '').startsWith('FALLBACK-'))
        const scen = persistedMode === 'SCENARIO' || hasScenarioIds
        const fb = persistedMode === 'FALLBACK' || hasFallbackIds
        setData({ spill_id: id, incident: { spill_detected: true, detected_at: raw.spill.detected_at, detection: { confidence: raw.spill.confidence, satellite: raw.spill.satellite }, slick: { geometry: raw.spill.geometry, area_km2: raw.spill.area_km2, centroid: raw.spill.centroid, bbox: [raw.spill.bbox_min_lon, raw.spill.bbox_min_lat, raw.spill.bbox_max_lon, raw.spill.bbox_max_lat] }, model: raw.spill.model }, origin: raw.drift ? { origin_centroid: raw.drift.origin_centroid, uncertainty_radius_km: raw.drift.uncertainty_radius_km, uncertainty_ellipse: raw.drift.uncertainty_ellipse, origin_probability: raw.drift.origin_probability, release_time_window_start: raw.drift.release_time_window[0], release_time_window_end: raw.drift.release_time_window[1], particle_count: raw.drift.particle_count, final_particle_positions: raw.drift.ensemble?.coordinates || [], particle_trajectories: raw.drift.trajectory_history || [], quality_score: raw.drift.quality_score, warnings: raw.drift.warnings || [], env_mode: raw.drift.env_mode } : null, vessels: raw.vessels, evidence: raw.vessels.flatMap(v => v.evidence || []), using_synthetic_environment: raw.drift?.using_synthetic_environment ?? false, using_fallback_ais: fb, ais_source: scen ? 'scenario' : 'gfw', data_mode: persistedMode, message: 'Loaded saved investigation' })
      } else {
        setData(raw)
      }
      setTab('overview'); logEvent(`Loaded investigation ${id}`, 'success')
    } catch (e) { setError(`Failed to load investigation: ${e.message}`); logEvent(`Load investigation error: ${e.message}`, 'error') } finally { setBusy(false) }
  }

  const pipelineState = error ? 'Attention required' : busy ? 'Running' : data ? 'Completed' : 'Ready'
  const healthLabel = !health ? 'Checking…' : health.status === 'ok' ? `Ready · ${health.model || 'detection model ready'}` : 'Unavailable'

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand">
        <div className="logo" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.5s5.2 5.3 5.2 9.3a5.2 5.2 0 1 1-10.4 0C6.8 8.8 12 3.5 12 3.5Z" fill="currentColor" /><path d="M18.5 6.5a8 8 0 0 1 0 12M21 4a11.5 11.5 0 0 1 0 17" stroke="#2b8790" strokeWidth="1.3" strokeLinecap="round" /></svg>
        </div>
        <div><b>OilTrace</b><small>Maritime Environmental Monitoring</small></div>
      </div>
      <nav className="masthead-nav" aria-label="Sections">
        <a href="#" className="active" onClick={(e) => e.preventDefault()}>Dashboard</a>
        <a href="/analysis" onClick={(e) => { e.preventDefault(); navigate('/analysis') }}>Analysis</a>
        <a href="/tracking" onClick={(e) => { e.preventDefault(); navigate('/tracking') }}>Vessel Tracking</a>
        <a href="/history" onClick={(e) => { e.preventDefault(); navigate('/history') }}>Historical Data</a>
        <a href="/reports" onClick={(e) => { e.preventDefault(); navigate('/reports') }}>Reports</a>
        <a href="/docs" onClick={(e) => { e.preventDefault(); navigate('/docs') }}>Documentation</a>
      </nav>
      <div className="top-status">
        <StatusPill ok={health?.status === 'ok'}>{`System Status: ${healthLabel}`}</StatusPill>
        <button className="dash-home-btn" onClick={() => navigate('/')} title="Back to front page">← Home</button>
        <button className="dash-home-btn" onClick={checkHealth}>Refresh status</button>
        <a href="#help-dash" className="dash-docs-link" onClick={(e) => { e.preventDefault(); document.getElementById('help-dash')?.scrollIntoView({ behavior: 'smooth' }) }}>Help</a>
      </div>
    </header>
    <div className="context-strip">
      <span><b>Dashboard</b> · Investigation workspace · Satellite Analysis / Spill Detection / Vessel Attribution</span>
      <span style={{ marginLeft: 'auto' }}>Pipeline state: <b>{pipelineState}</b>{busy ? ` · Running ${elapsed.toFixed(0)} s` : ''}</span>
    </div>
    <div className="layout">
      {/* LEFT PANEL */}
      <aside className="left-panel" aria-label="Investigation input">
        <section className="card input-card" aria-labelledby="inv-input">
          <div className="section-head" id="inv-input"><div className="dash-card-title"><span className="section-num">1</span><b>Investigation Input</b></div><span>Data source</span></div>
          <div className="dash-source-toggle" role="tablist" aria-label="Data source">
            <button type="button" role="tab" aria-selected={form.source === 'aws'} className={form.source === 'aws' ? 'active' : ''} onClick={() => update('source', 'aws')}>Sentinel-1 archive</button>
            <button type="button" role="tab" aria-selected={form.source === 'local'} className={form.source === 'local' ? 'active' : ''} onClick={() => update('source', 'local')}>File upload</button>
          </div>
          {form.source === 'aws' ? (
            <div className="aws-input-flow">
              <div className="input-group">
                <div className="input-group-header">
                  <label className="input-label" htmlFor="aoi-box">Area of Interest (AOI)</label>
                  {bbox && <button type="button" className="aoi-clear-btn" onClick={() => update('aoi', null)}>Clear</button>}
                </div>
                <div id="aoi-box" className={`aoi-status-display ${bbox ? 'defined' : ''}`}>
                  {bbox ? <span className="aoi-text-coords">{bbox.map(v => Number(v).toFixed(3)).join(', ')}</span> : <span className="aoi-text-empty">Select on the map (Select AOI, two clicks) or choose a preset.</span>}
                </div>
                <div className="preset-row">
                  {REGION_PRESETS.map(r => {
                    const isSelected = form.aoi && Math.abs(form.aoi[0][0] - r.aoi[0][0]) < 0.05 && Math.abs(form.aoi[0][1] - r.aoi[0][1]) < 0.05 && Math.abs(form.aoi[1][0] - r.aoi[1][0]) < 0.05 && Math.abs(form.aoi[1][1] - r.aoi[1][1]) < 0.05
                    return <button key={r.name} type="button" className={`preset-btn ${isSelected ? 'active' : ''}`} onClick={() => update('aoi', isSelected ? null : r.aoi)} title={isSelected ? `Clear ${r.name}` : `Select ${r.name}`}>{r.name}</button>
                  })}
                </div>
              </div>
              <div className="input-group">
                <label className="input-label">Polarisation</label>
                <div className="pol-toggle-row" role="radiogroup" aria-label="Polarisation">
                  {['vv', 'vh', 'hh', 'hv'].map(p => <button key={p} type="button" role="radio" aria-checked={form.polarization === p} className={`pol-btn ${form.polarization === p ? 'active' : ''}`} onClick={() => update('polarization', p)}>{p.toUpperCase()}</button>)}
                </div>
              </div>
              <div className="input-group">
                <div className="input-group-header">
                  <label className="input-label">Acquisition Window (UTC)</label>
                  <span className="window-duration-tag">{datePreset === 'custom' ? 'Custom range' : `Last ${datePreset.replace('d', '')} days`}</span>
                </div>
                <div className="date-preset-pills">
                  {[{ id: '7d', label: '7 days', days: 7 }, { id: '14d', label: '14 days', days: 14 }, { id: '30d', label: '30 days', days: 30 }, { id: 'custom', label: 'Custom' }].map(p => (
                    <button key={p.id} type="button" className={`date-preset-pill ${datePreset === p.id ? 'active' : ''}`} onClick={() => { if (p.days) applyDatePreset(p.days); else setDatePreset('custom') }}>{p.label}</button>
                  ))}
                </div>
                {datePreset !== 'custom' ? (
                  <div className="date-window-summary" onClick={() => setDatePreset('custom')} title="Select to enter custom dates">
                    <div className="date-window-row"><span className="date-window-badge">From → To</span><span className="date-window-sub">Edit</span></div>
                    <div className="date-timeline-stack">
                      <div className="date-timeline-node"><span className="dt-tag">FROM</span><span className="dt-val">{formatDisplayDate(form.start)}</span></div>
                      <div className="date-timeline-node"><span className="dt-tag">TO</span><span className="dt-val">{formatDisplayDate(form.end)}</span></div>
                    </div>
                  </div>
                ) : (
                  <div className="custom-date-container">
                    <div className="custom-date-row">
                      <div className="custom-date-col"><label className="custom-date-sublabel" htmlFor="from-dt">From (UTC)</label><input id="from-dt" type="datetime-local" className="dash-dt-input" value={form.start} onChange={e => { setDatePreset('custom'); update('start', e.target.value) }} /></div>
                      <div className="custom-date-col"><label className="custom-date-sublabel" htmlFor="to-dt">To (UTC)</label><input id="to-dt" type="datetime-local" className="dash-dt-input" value={form.end} onChange={e => { setDatePreset('custom'); update('end', e.target.value) }} /></div>
                    </div>
                    <button type="button" className="reset-preset-link" onClick={() => applyDatePreset(7)}>Reset to 7 days</button>
                  </div>
                )}
              </div>
              <button type="button" className="primary full search-archive-btn" disabled={busy} onClick={searchScenes}>{busy ? 'Searching archive…' : 'Search archive'}</button>
              <div className="scene-results-container">
                <div className="scene-results-head"><span>Satellite scenes</span><span className="scene-count-tag">{scenes.length} found</span></div>
                <div className="scene-list">
                  {scenes.map(s => {
                    const isSelected = form.scene?.id === s.id
                    return (
                      <div key={s.id} className={`scene ${isSelected ? 'selected' : ''}`}>
                        <div className="scene-top-row"><b title={s.id}>{s.id}</b><span className="scene-asset-badge">{s.asset_key || form.polarization.toUpperCase()}</span></div>
                        <small>{s.datetime ? new Date(s.datetime).toLocaleString('en-GB') : 'Acquisition time unavailable'} · {s.instrument_mode || 'IW GRD'}</small>
                        <button type="button" className={isSelected ? 'active-select' : ''} onClick={() => { update('scene', s); update('detectedAt', s.datetime?.slice(0, 16) || ''); if (!bbox && s.bbox?.length === 4) update('aoi', [[s.bbox[0], s.bbox[1]], [s.bbox[2], s.bbox[3]]]) }}>{isSelected ? 'Selected scene' : 'Select scene'}</button>
                      </div>
                    )
                  })}
                  {!scenes.length && <small className="muted">No scenes listed yet. Run an archive search.</small>}
                </div>
              </div>
            </div>
          ) : (
            <div className="local-upload-flow">
              <label className="dash-drop-card"><input type="file" accept=".tif,.tiff,.png,.jpg,.jpeg" onChange={e => update('file', e.target.files?.[0] || null)} /><strong>{form.file ? form.file.name : 'Select GeoTIFF / PNG / JPG'}</strong><small>GeoTIFF bounds are read automatically. Plain images require manual bounds.</small></label>
              <div className="two" style={{ marginTop: '10px' }}>
                <div><label className="input-label">Min lon</label><input placeholder="Min lon" value={localBounds[0]} onChange={e => setLocalBounds([e.target.value, localBounds[1], localBounds[2], localBounds[3]])} /></div>
                <div><label className="input-label">Min lat</label><input placeholder="Min lat" value={localBounds[1]} onChange={e => setLocalBounds([localBounds[0], e.target.value, localBounds[2], localBounds[3]])} /></div>
                <div><label className="input-label">Max lon</label><input placeholder="Max lon" value={localBounds[2]} onChange={e => setLocalBounds([localBounds[0], localBounds[1], e.target.value, localBounds[3]])} /></div>
                <div><label className="input-label">Max lat</label><input placeholder="Max lat" value={localBounds[3]} onChange={e => setLocalBounds([localBounds[0], localBounds[1], localBounds[2], e.target.value])} /></div>
              </div>
            </div>
          )}
        </section>

        <section className="card" id="analysis-params" aria-labelledby="analysis-h">
          <div className="section-head" id="analysis-h"><div className="dash-card-title"><span className="section-num">2</span><b>Analysis Parameters</b></div><span>Drift + AIS</span></div>
          <div className="input-group">
            <div className="input-group-header">
              <label className="input-label" htmlFor="detect-dt">Detection time (UTC)</label>
              <button type="button" className="set-now-btn" onClick={() => update('detectedAt', toIsoSlice(new Date()))}>Set now</button>
            </div>
            <input id="detect-dt" type="datetime-local" className="dash-dt-input" value={form.detectedAt} onChange={e => update('detectedAt', e.target.value)} />
            {form.scene && <div className="scene-synced-badge">Scene acquisition applied: {formatDisplayDate(form.detectedAt)}</div>}
          </div>
          <div className="param-grid-row">
            <div className="param-field"><label className="input-label" htmlFor="lookback">Drift lookback (hours, 1–168)</label><div className="input-unit-wrapper"><input id="lookback" type="number" min="1" max="168" value={form.lookback} onChange={e => update('lookback', Number(e.target.value) || 1)} /><span className="input-unit-tag">hrs</span></div></div>
            <div className="param-field"><label className="input-label" htmlFor="radius">AIS radius (km, 1–200)</label><div className="input-unit-wrapper"><input id="radius" type="number" min="1" max="200" value={form.radius} onChange={e => update('radius', Number(e.target.value) || 1)} /><span className="input-unit-tag">km</span></div></div>
          </div>
          <div className="input-group" style={{ marginTop: '12px' }}>
            <label className="input-label">AIS candidate source</label>
            <div className="ais-source-toggle">
              <button type="button" className={`ais-toggle-btn ${form.aisSource === 'scenario' ? 'active' : ''}`} onClick={() => update('aisSource', 'scenario')}><div className="ais-toggle-header"><span className="ais-status-dot scenario" /><b>Simulated fleet</b></div><small>Curated vessel records</small></button>
              <button type="button" className={`ais-toggle-btn ${form.aisSource === 'gfw' ? 'active' : ''}`} onClick={() => update('aisSource', 'gfw')}><div className="ais-toggle-header"><span className="ais-status-dot gfw" /><b>External AIS</b></div><small>External AIS service</small></button>
            </div>
          </div>
          <button className="primary full run-pipeline-btn" disabled={busy || (form.source === 'aws' && !form.scene) || (form.source === 'local' && !form.file)} onClick={run}>{busy ? `Running… ${elapsed.toFixed(0)} s` : form.source === 'aws' && !form.scene ? 'Select a scene to run' : 'Run analysis pipeline'}</button>
          {error && <div className="error-box" role="alert">{error}</div>}
        </section>

        <section className="card" aria-labelledby="pipe-h"><div className="section-head" id="pipe-h"><div className="dash-card-title"><span className="section-num">3</span><b>Pipeline Status</b></div><StatusPill ok={!error} warn={!!error}>{pipelineState}</StatusPill></div><StageRail active={stage} error={error ? stage : null} statuses={stageStatuses} /></section>
        <section className="card" aria-labelledby="log-h"><div className="section-head" id="log-h"><div className="dash-card-title"><span className="section-num">4</span><b>Activity Log</b></div><span>{events.length} events</span></div><div className="event-log">{events.length ? events.map((e, i) => <div className={`event ${e.kind}`} key={i}><time>{new Date(e.time).toLocaleTimeString('en-GB')}</time><span>{e.message}</span></div>) : <small className="muted">No activity yet.</small>}</div></section>
        <section className="card" id="saved" aria-labelledby="saved-h">
          <div className="section-head" id="saved-h"><div className="dash-card-title"><span className="section-num">5</span><b>Saved Investigations</b></div><button onClick={loadHistory}>Refresh</button></div>
          {history.length ? (
            <div className="ot-table-wrap"><table className="ot-table"><thead><tr><th>Investigation ID</th><th className="num">Detected</th><th className="num">Area km²</th><th>Action</th></tr></thead><tbody>
              {history.slice(0, 10).map(x => (
                <tr key={x.id}><td className="mono">{x.id}</td><td className="num">{x.detected_at ? new Date(x.detected_at).toLocaleDateString('en-GB') : '—'}</td><td className="num">{Number(x.area_km2 || 0).toFixed(2)}</td><td><button className="ot-row-btn" onClick={() => loadIncident(x.id)}>Open</button></td></tr>
              ))}
            </tbody></table></div>
          ) : <small className="muted">No saved investigations.</small>}
        </section>
        <section className="card" id="help-dash"><div className="section-head"><div className="dash-card-title"><span className="section-num">6</span><b>Help</b></div></div><div className="prototype-note">OilTrace is an independent research prototype. Search the archive, select a scene, set detection time and drift/AIS parameters, then run the pipeline. Map: use Select AOI and click two corners. Vessel rows select and highlight tracks. Exports produce GeoJSON, CSV, and PDF for the loaded investigation.</div></section>
      </aside>

      {/* CENTER */}
      <main className="center" aria-label="Geospatial analysis workspace">
        <MapView data={data} aoi={form.aoi} selectedScene={form.scene} onAoiChange={handleAoiChange} selectedVesselId={selectedVesselId} onSelectVessel={setSelectedVesselId} />
        <div className="map-tip">{data ? `${data.message || 'Analysis result'} · ${data.ais_source === 'scenario' ? 'Simulated data' : data.using_fallback_ais ? 'AIS fallback used' : 'Live data'} · ${(data.processing_seconds ?? elapsed).toFixed ? Number(data.processing_seconds ?? elapsed).toFixed(1) : data.processing_seconds}s` : busy ? `Searching / Running… ${elapsed.toFixed(1)} s` : 'Search the archive and select a scene, or upload an image, then run the pipeline.'}</div>
      </main>

      {/* RIGHT PANEL */}
      <aside className="right-panel" aria-label="Analysis results">
        <SelectedScene scene={form.scene} data={data} />
        <div className="card metrics" aria-label="Detection metrics">
          <Metric label="spill area km²" value={data?.incident?.slick?.area_km2?.toFixed?.(2)} />
          <Metric label="model confidence" value={data?.incident?.detection?.confidence ? (data.incident.detection.confidence * 100).toFixed(1) : null} suffix="%" />
          <Metric label="origin uncertainty km" value={data?.origin?.uncertainty_radius_km?.toFixed?.(2)} />
          <Metric label="AIS candidates" value={data?.vessels?.length ?? null} />
          <Metric label="processing time" value={data?.processing_seconds?.toFixed?.(1) ?? (busy ? elapsed.toFixed(1) : null)} suffix="s" />
        </div>
        <div className="card">
          <div className="section-head"><div className="dash-card-title"><span className="section-num">7</span><b>Analysis Results</b></div></div>
          <div className="tabs" role="tablist">{[['overview', 'Overview'], ['model', 'Model output'], ['backtrack', busy && data?.origin ? 'Backtracking · playback' : 'Backtracking'], ['timeline', 'Timeline'], ['raw', 'Raw JSON']].map(([id, label]) => <button role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)} key={id}>{label}</button>)}</div>
          {tab === 'overview' && <Overview data={data} />}
          {tab === 'model' && <ModelOutput data={data} />}
          {tab === 'backtrack' && <DriftAnimator data={data} />}
          {tab === 'timeline' && <Timeline data={data} />}
          {tab === 'raw' && <pre className="json">{data ? JSON.stringify(data, null, 2) : 'No investigation loaded.'}</pre>}
        </div>
        <div id="candidates"><Candidates data={data} selectedVesselId={selectedVesselId} onSelectVessel={setSelectedVesselId} /></div>
        <div className="card" id="exports"><div className="section-head"><div className="dash-card-title"><span className="section-num">8</span><b>Data Export</b></div><span>{data ? `ID ${data.spill_id}` : 'no investigation'}</span></div>
          <div className="three"><button disabled={!data} onClick={() => downloadFile(`/api/v1/export/${data.spill_id}?fmt=geojson`, `${data.spill_id}.geojson`)}>GeoJSON</button><button disabled={!data} onClick={() => downloadFile(`/api/v1/export/${data.spill_id}?fmt=csv`, `${data.spill_id}.csv`)}>CSV</button><button disabled={!data} onClick={() => downloadFile(`/api/v1/report/${data.spill_id}`, `${data.spill_id}.pdf`)}>PDF report</button></div>
          <div style={{ marginTop: 8 }}><small className="muted">Exports reflect the loaded investigation result. PDF is an analysis summary from a research prototype.</small></div>
        </div>
      </aside>
    </div>
  </div>
}

function Overview({ data }) {
  if (!data) return <div className="empty-panel">No investigation loaded.</div>
  const isScenario = data.ais_source === 'scenario'
  return <div className="overview">
    <div className="source-line">
      <StatusPill ok={!data.using_synthetic_environment}>{data.using_synthetic_environment ? (isScenario ? 'Simulated data' : 'Fallback data') : 'Live data'}</StatusPill>
      <StatusPill ok={!data.using_fallback_ais}>{isScenario ? 'Simulated data' : data.using_fallback_ais ? 'AIS fallback used' : 'Live data'}</StatusPill>
    </div>
    <div className="summary"><b>{data.incident?.detection?.satellite || 'SAR'} slick candidate</b><span>{data.incident?.detected_at ? new Date(data.incident.detected_at).toLocaleString('en-GB') : '—'}</span></div>
    <div className="facts">
      <div><span>Centroid</span><b>{data.incident?.slick?.centroid?.map(x => Number(x).toFixed(5)).join(', ') || '—'}</b></div>
      <div><span>Origin</span><b>{data.origin?.origin_centroid?.map(x => Number(x).toFixed(5)).join(', ') || '—'}</b></div>
      <div><span>Release window</span><b>{data.origin?.release_time_window_start ? `${new Date(data.origin.release_time_window_start).toLocaleString('en-GB')} → ${new Date(data.origin.release_time_window_end).toLocaleString('en-GB')}` : '—'}</b></div>
    </div>
  </div>
}

function SelectedScene({ scene, data }) {
  if (!scene) return <div className="card selected-scene"><div className="section-head"><div className="dash-card-title"><span className="section-num">A</span><b>Selected Scene</b></div><StatusPill>None</StatusPill></div><small className="muted">Search the archive and select a Sentinel-1 scene.</small></div>
  const bbox = scene.bbox || []
  const processedUrl = data?.spill_id ? `${API_BASE}/api/v1/incidents/${encodeURIComponent(data.spill_id)}/assets/normalized.png` : null
  return <div className="card selected-scene"><div className="section-head"><div className="dash-card-title"><span className="section-num">A</span><b>Selected Scene</b></div><StatusPill ok>Sentinel-1</StatusPill></div>
    {scene.preview_url && <img className="sar-preview" src={scene.preview_url} alt={`SAR preview for ${scene.id}`} onError={e => { e.currentTarget.hidden = true; e.currentTarget.nextElementSibling.hidden = false }} />}
    <small className="sar-unavailable" hidden>Archive preview unavailable; the georeferenced footprint is still shown on the map.</small>
    {processedUrl && <><img className="sar-preview" src={processedUrl} alt="Processed SAR image used by the detector" /><small className="sar-caption">Processed SAR image used by the detector</small></>}
    <strong>{scene.id}</strong>
    <div className="scene-meta"><span>{scene.datetime ? new Date(scene.datetime).toLocaleString('en-GB') : 'Acquisition time unavailable'}</span><span>{String(scene.asset_key || 'unknown').toUpperCase()} · {scene.instrument_mode || 'GRD'} · {scene.platform || 'Sentinel-1'}</span></div>
    {bbox.length === 4 && <div className="scene-bbox"><span>Scene footprint (min lon, min lat, max lon, max lat)</span><b>{bbox.map(value => Number(value).toFixed(4)).join(', ')}</b></div>}
  </div>
}

function ModelOutput({ data }) {
  const m = data?.incident?.model
  if (!m) return <div className="empty-panel">Run detection to inspect model output diagnostics.</div>
  const base = `${API_BASE}/api/v1/incidents/${encodeURIComponent(data.spill_id)}/assets`
  return <>
    {data?.data_mode === 'SCENARIO' && <div className="inline-warning">Prototype segmentation: simulated mask and overlay for walkthrough use, not a verified real spill detection.</div>}
    <div className="model-grid">
      <div><b>Model name</b><span>{m.model_name || 'oil-spill U-Net'}</span></div>
      <div><b>Threshold</b><span>{m.threshold ?? 0.5}</span></div>
      <div><b>Positive pixels</b><span>{m.positive_pixels ?? '—'}</span></div>
      <div><b>Positive area</b><span>{m.positive_percent?.toFixed?.(2) ?? '—'}%</span></div>
      <div><b>Probability range</b><span>{m.probability_min?.toFixed?.(4) ?? '—'} → {m.probability_max?.toFixed?.(4) ?? '—'}</span></div>
      <div><b>Connected components</b><span>{m.connected_components ?? '—'}</span></div>
    </div>
    {m.prediction_rejected && <div className="inline-warning">Detection mask rejected: {m.guard_reason || 'model output failed the scene-size quality guard.'}</div>}
    <div className="asset-grid">{[['normalized', 'Normalized image'], ['probability', 'Probability image'], ['mask', 'Mask'], ['overlay', 'Overlay']].map(([name, label]) => <figure key={name}><img src={`${base}/${name}.png`} alt={label} loading="lazy" /><figcaption>{label}</figcaption></figure>)}</div>
  </>
}

function num(v, digits = 1) { const n = Number(v); return Number.isFinite(n) ? n.toFixed(digits) : '—' }

function Candidates({ data, selectedVesselId, onSelectVessel }) {
  const vessels = data?.vessels || []
  return (
    <div className="card">
      <div className="section-head"><div className="dash-card-title"><span className="section-num">B</span><b>Candidate Vessels</b></div><span>{vessels.length} candidates · explainable analysis</span></div>
      {!vessels.length ? <small className="muted">No candidates yet. Run AIS matching.</small> : (
        <div className="ot-table-wrap"><table className="ot-table">
          <thead><tr><th>Rank</th><th>Vessel</th><th>Type</th><th>MMSI</th><th className="num">Spatial</th><th className="num">Temporal</th><th className="num">Traj.</th><th className="num">Total</th><th>Details</th></tr></thead>
          <tbody>
            {vessels.slice(0, 12).map((v) => {
              const isSelected = v.vessel_id === selectedVesselId
              // Live job results carry factors as a list of {factor, raw_score,
              // status}; saved incidents carry a dict of raw numbers. Normalize
              // both shapes and render unavailable factors honestly as n/a.
              const factorStatus = {}
              if (Array.isArray(v.factors)) {
                for (const x of v.factors) factorStatus[x.factor] = x.status
              }
              const fmap = Array.isArray(v.factors)
                ? Object.fromEntries(v.factors.map(x => [x.factor, x.raw_score]))
                : (v.factors || {})
              const fcell = (name) => {
                if (factorStatus[name] === 'UNAVAILABLE' || (v.unavailable || []).includes(name)) return 'n/a'
                const val = fmap[name]
                const s = num(val, 0)
                return (factorStatus[name] === 'DEGRADED' ? s + '*' : s)
              }
              return (
                <tr key={v.vessel_id} className={isSelected ? 'selected' : ''} onClick={() => onSelectVessel?.(isSelected ? null : v.vessel_id)} style={{ cursor: 'pointer' }}>
                  <td className="num">#{v.rank ?? '—'}</td>
                  <td>{formatVesselName(v.name, v.mmsi)}<br /><small className="muted">{v.vessel_id}</small></td>
                  <td>{v.vessel_type || '—'}</td>
                  <td className="mono">{formatMmsi(v.mmsi)}</td>
                  <td className="num">{fcell('proximity')}</td>
                  <td className="num">{fcell('timing')}</td>
                  <td className="num">{fcell('trajectory')}</td>
                  <td className="num"><b>{num(v.total_score)}</b></td>
                  <td><button className="ot-row-btn" onClick={(e) => { e.stopPropagation(); onSelectVessel?.(isSelected ? null : v.vessel_id) }}>{isSelected ? 'Hide' : 'Show'}</button></td>
                </tr>
              )
            })}
          </tbody>
        </table></div>
      )}
      <div style={{ marginTop: 8 }}><small className="muted">Neutral analysis ranking: candidate vessel · attribution score · spatial/temporal correlation · potential source. No finding of responsibility. n/a = factor unavailable for the provider capability (excluded from total, weights renormalized); * = degraded factor (approximate).</small></div>
    </div>
  )
}
