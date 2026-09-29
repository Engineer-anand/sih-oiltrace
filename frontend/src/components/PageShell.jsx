import { Link, useLocation } from 'react-router-dom'
import NoticeBar from './NoticeBar.jsx'

const LINKS = [
  ['/dashboard', 'Dashboard'],
  ['/analysis', 'Analysis'],
  ['/detection', 'Detection'],
  ['/drift', 'Backward Drift'],
  ['/tracking', 'Vessel Tracking'],
  ['/candidates', 'AIS Candidates'],
  ['/history', 'Historical Data'],
  ['/reports', 'Reports'],
  ['/docs', 'Documentation'],
]

export function ModeChip({ mode }) {
  if (!mode) return null
  const cls = mode === 'LIVE' ? 'ok' : mode === 'SCENARIO' ? 'warn' : 'info'
  const label = mode === 'LIVE' ? 'Live data' : mode === 'SCENARIO' ? 'Simulated data'
    : mode === 'FALLBACK' ? 'Fallback data' : mode === 'ARCHIVE' ? 'Archived data'
      : mode === 'DEGRADED' ? 'Degraded' : mode
  return <span className={`status-pill ${cls}`}>{label}</span>
}

export default function PageShell({ title, section, crumb, mode, children }) {
  const loc = useLocation()
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="logo" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3.5s5.2 5.3 5.2 9.3a5.2 5.2 0 1 1-10.4 0C6.8 8.8 12 3.5 12 3.5Z" fill="currentColor" /><path d="M18.5 6.5a8 8 0 0 1 0 12M21 4a11.5 11.5 0 0 1 0 17" stroke="#2b8790" strokeWidth="1.3" strokeLinecap="round" /></svg>
          </div>
          <div><b>OilTrace</b><small>Maritime Environmental Monitoring</small></div>
        </div>
        <nav className="masthead-nav" aria-label="Sections">
          {LINKS.map(([to, label]) => (
            <Link key={to} to={to} className={loc.pathname === to ? 'active' : ''}>{label}</Link>
          ))}
        </nav>
      </header>
      {/* Formal tri-band rule beneath the masthead. Colour discipline only —
          no emblem, flag or authority mark of any kind. */}
      <div className="ot-bands" aria-hidden="true">
        <span /><span /><span />
      </div>
      <div className="context-strip">
        <Link to="/">OilTrace</Link><span aria-hidden="true"> › </span><span>{crumb || title}</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, alignItems: 'center' }}>
          <ModeChip mode={mode} />
          <span className="muted">Independent research prototype</span>
        </span>
      </div>
      <NoticeBar mode={mode} />
      <main className="page-layout" aria-label={section || title}>
        <div className="card">
          <div className="section-head">
            <div className="dash-card-title"><b>{title}</b></div>
          </div>
          {children}
        </div>
      </main>
      <Footer />
    </div>
  )
}

/**
 * Shared footer. The disclaimer is deliberately explicit and always visible:
 * the interface is styled to an institutional standard, and it must never be
 * possible to read that styling as a claim of official status.
 */
export function Footer() {
  return (
    <footer className="ot-footer">
      <div className="ot-footer-main">
        <div>
          <b>OilTrace</b>
          <span className="muted"> — satellite SAR oil-spill detection and vessel attribution</span>
        </div>
        <div className="ot-footer-links">
          <a href="/docs" target="_blank" rel="noopener noreferrer">API reference</a>
          <a href="/health" target="_blank" rel="noopener noreferrer">System status</a>
          <a href="/metrics" target="_blank" rel="noopener noreferrer">Metrics</a>
        </div>
      </div>
      <p className="ot-footer-note">
        Independent research prototype. Not affiliated with, endorsed by, or operated on behalf of any
        government body, agency or authority. Attribution scores express statistical association between
        vessels and an estimated origin; they are investigative indicators and do not establish responsibility.
      </p>
    </footer>
  )
}

export function Loading({ label }) {
  return <p className="muted" role="status">{label || 'Loading…'}</p>
}

export function ErrorBox({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="error-box" role="alert">
      <span>{typeof error === 'string' ? error : error.message || 'Request failed'}</span>
      {onRetry ? <button type="button" className="ot-row-btn" onClick={onRetry}>Retry</button> : null}
    </div>
  )
}

export function Empty({ children }) {
  return <div className="empty-panel"><small className="muted">{children}</small></div>
}
