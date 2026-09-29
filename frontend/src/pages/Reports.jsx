import { useState } from 'react'
import PageShell, { Empty, ErrorBox, ModeChip } from '../components/PageShell.jsx'
import { downloadFile } from '../lib/api.js'
import { SpillSelector, useSpills } from './Analysis.jsx'

export default function Reports() {
  const { spills, error: listError } = useSpills()
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(null)

  const sel = spills?.find((s) => s.id === selected)

  const fetch = async (kind) => {
    setError('')
    setBusy(kind)
    try {
      if (kind === 'pdf') await downloadFile(`/api/v1/report/${encodeURIComponent(selected)}`, `${selected}_report.pdf`)
      else if (kind === 'geojson') await downloadFile(`/api/v1/export/${encodeURIComponent(selected)}?fmt=geojson`, `${selected}.geojson`)
      else await downloadFile(`/api/v1/export/${encodeURIComponent(selected)}?fmt=csv`, `${selected}_candidates.csv`)
    } catch (e) { setError(`Export failed: ${e.message}`) }
    finally { setBusy(null) }
  }

  return (
    <PageShell title="Reports & Exports" crumb="Reports" mode={sel?.data_mode}>
      <p className="muted">Rendered reports carry data mode, methodology, provenance, and limitations. Generated files are versioned server-side with checksums.</p>
      <ErrorBox error={listError || error} />
      {!spills?.length ? <Empty>No saved investigations yet. Run the pipeline from the Dashboard.</Empty> : (
        <>
          <SpillSelector spills={spills} value={selected} onChange={setSelected} />
          {sel ? (
            <>
              <div style={{ margin: '8px 0' }}><ModeChip mode={sel.data_mode} /></div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button type="button" className="ot-btn" disabled={busy} onClick={() => fetch('pdf')}>{busy === 'pdf' ? 'Preparing…' : 'Download PDF report'}</button>
                <button type="button" className="ot-btn" disabled={busy} onClick={() => fetch('geojson')}>{busy === 'geojson' ? 'Preparing…' : 'Download GeoJSON'}</button>
                <button type="button" className="ot-btn" disabled={busy} onClick={() => fetch('csv')}>{busy === 'csv' ? 'Preparing…' : 'Download CSV'}</button>
              </div>
            </>
          ) : <Empty>Select an investigation to export its report.</Empty>}
        </>
      )}
    </PageShell>
  )
}
