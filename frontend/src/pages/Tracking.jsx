import { useState } from 'react'
import PageShell, { Empty, ErrorBox, Loading } from '../components/PageShell.jsx'
import TrackingMap from '../components/TrackingMap.jsx'
import { apiJson } from '../lib/api.js'

export default function Tracking() {
  const [mmsi, setMmsi] = useState('')
  const [history, setHistory] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const lookup = async (e) => {
    e?.preventDefault()
    if (!mmsi.trim()) return
    setLoading(true); setError(''); setHistory(null)
    try {
      const q = new URLSearchParams({ mmsi: mmsi.trim() })
      setHistory(await apiJson('/api/v1/ais/history?' + q))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }

  return (
    <PageShell title="Vessel Tracking" crumb="Vessel Tracking">
      <p className="muted">Ordered position history for one vessel identity. History reflects periodic refresh, not a live stream. Synthetic identities are labeled and excluded from real-data queries by default.</p>
      <form onSubmit={lookup} className="input-group" style={{ maxWidth: 420 }}>
        <label className="input-label" htmlFor="mmsi">MMSI or vessel identity</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input id="mmsi" className="ot-input" value={mmsi} onChange={(e) => setMmsi(e.target.value)} placeholder="Enter MMSI" />
          <button type="submit" className="ot-btn">Look up</button>
        </div>
      </form>
      <ErrorBox error={error} onRetry={lookup} />
      {loading ? <Loading label="Loading history…" /> : history ? (
        <>
          <div className="model-grid">
            <div><small className="muted">Identity</small><br /><b className="mono">{history.identity_key}</b>{history.synthetic ? ' (synthetic)' : ''}</div>
            <div><small className="muted">Fixes</small><br /><b>{history.fix_count}</b></div>
            <div><small className="muted">Segments</small><br /><b>{history.segment_count}</b></div>
            <div><small className="muted">Last seen (UTC)</small><br /><b>{history.last_seen || '—'}</b></div>
            <div><small className="muted">Freshness</small><br /><b>{history.freshness}{history.stale ? ' (stale)' : ''}</b></div>
          </div>
          <p className="muted">{history.note}</p>
          <TrackingMap history={history} />
          {history.segments?.length ? (
            <div className="ot-table-wrap"><table className="ot-table">
              <thead><tr><th>#</th><th>Fixes</th><th className="num">First (UTC)</th><th className="num">Last (UTC)</th></tr></thead>
              <tbody>
                {history.segments.map((seg, i) => (
                  <tr key={i}><td className="num">{i + 1}</td><td className="num">{seg.length}</td>
                    <td className="num mono">{seg[0]?.ts}</td><td className="num mono">{seg[seg.length - 1]?.ts}</td></tr>
                ))}
              </tbody>
            </table></div>
          ) : <Empty>No position history for this identity in the selected window.</Empty>}
        </>
      ) : <Empty>Enter an identity to view its recorded history.</Empty>}
    </PageShell>
  )
}
