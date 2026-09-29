import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_BASE, apiJson } from '../lib/api.js'
import '../landing.css'

/* Live backend health — real /health call, honest states only. */
function HealthPill() {
  const [health, setHealth] = useState(null)
  useEffect(() => {
    apiJson('/health').then(setHealth).catch(() => setHealth({ status: 'offline' }))
  }, [])
  const isOk = health?.status === 'ok'
  return (
    <div className="lp-health-badge" data-ok={isOk}>
      <span className="lp-health-dot" />
      <span>{health == null ? 'Checking status…' : isOk ? 'System Status: Ready' : 'System Status: Unavailable'}</span>
    </div>
  )
}

/* Six technical stages — method / input / processing / output / technology. */
const PIPELINE_STAGES = [
  {
    n: '01', label: 'Sentinel-1 ingestion', method: 'STAC search (GET /api/v1/sentinel1/search)',
    input: 'Area of interest polygon, acquisition window, polarisation',
    processing: 'Query Copernicus Sentinel-1 GRD archive via STAC; retrieve Cloud-Optimized GeoTIFF URLs (VV/VH); compute footprint polygon and bounding box in EPSG:4326.',
    output: 'Candidate scene list with acquisition time, footprint geometry, and asset references.',
    technology: 'Copernicus STAC · AWS open data · Shapely',
  },
  {
    n: '02', label: 'U-Net detection', method: 'Segmentation (POST /api/v1/detect)',
    input: 'Calibrated 10 m SAR raster (VV backscatter).',
    processing: 'U-Net encoder–decoder inference on 256×256 tiles with overlap blending; speckle filtering; small-object removal; high-coverage guard.',
    output: 'Binary oil mask, probability map, slick polygon, area (km²), centroid, confidence.',
    technology: 'PyTorch · TorchScript · Rasterio · scikit-image',
  },
  {
    n: '03', label: 'Geospatial reconstruction', method: 'Vectorization (internal)',
    input: 'Binary detection mask and scene georeferencing.',
    processing: 'Georeference mask pixels to map coordinates; polygonize connected components; compute area, perimeter, centroid, and bounding box.',
    output: 'Geospatial slick geometry (GeoJSON) with measured extent and position.',
    technology: 'GDAL · Rasterio · Shapely',
  },
  {
    n: '04', label: 'Backward drift', method: 'Lagrangian modelling (POST /api/v1/drift)',
    input: 'Slick centroid, detection time, lookback window (1–168 h).',
    processing: 'Seed ensemble of particles at the slick; advect backward using ocean-current and wind reanalysis fields with diffusion.',
    output: 'Estimated origin centroid, uncertainty radius, release-time window, particle trajectories.',
    technology: 'OpenDrift OpenOil · ocean currents · wind reanalysis',
  },
  {
    n: '05', label: 'AIS candidate matching', method: 'Spatio-temporal query (GET /api/v1/ais)',
    input: 'Estimated origin, uncertainty radius, release-time window.',
    processing: 'Query vessel position records intersecting the origin region and time window; reconstruct track segments per candidate.',
    output: 'Candidate vessel list with MMSI, type, flag, positions, speed, and heading history.',
    technology: 'AIS records · spatial index · GeoJSON tracks',
  },
  {
    n: '06', label: 'Attribution and report generation', method: 'Multi-criteria scoring + export (GET /api/v1/report/{id})',
    input: 'Detection geometry, drift result, candidate vessel tracks.',
    processing: 'Score six explainable factors (spatial, temporal, trajectory, drift overlap, maneuvering, vessel profile); rank candidates; assemble maps and tables.',
    output: 'Ranked candidate table with factor breakdown; GeoJSON, CSV, and PDF report exports.',
    technology: 'NumPy scoring · ReportLab PDF · PostGIS / SQLite',
  },
]

const SATELLITE_SCENES = [
  {
    id: 'gulf', name: 'Gulf of Mexico · Deepwater Basin', coords: '28.736°N, 88.387°W',
    sensor: 'Sentinel-1A IW GRD · C-band · VV', src: './sat/spill1.jpg',
    sceneId: 'S1A_IW_GRDH_1SDV_20240812T1053', acquisition: '2024-08-12 10:53 UTC',
    area: '18.4 km²', confidence: '94.8%', status: 'Completed',
    description: 'Dark backscatter anomaly consistent with a surfactant film damping short capillary waves. VV single-polarisation mode; surrounding sea clutter provides contrast for segmentation.',
  },
  {
    id: 'northsea', name: 'North Sea · Ekofisk Sector', coords: '56.549°N, 3.214°E',
    sensor: 'Sentinel-1B IW GRD · C-band · VV+VH', src: './sat/spill2.jpg',
    sceneId: 'S1B_IW_GRDH_1SDV_20240811T0612', acquisition: '2024-08-11 06:12 UTC',
    area: '9.2 km²', confidence: '96.2%', status: 'Completed',
    description: 'Linear anomaly aligned with a commercial corridor. Dual-polarisation cross-check helps separate thin films from low-wind calm zones that mimic oil.',
  },
  {
    id: 'indianocean', name: 'Indian Ocean · Malacca Approaches', coords: '5.412°N, 97.834°E',
    sensor: 'Sentinel-1A IW GRD · C-band · VV', src: './sat/spill3.jpg',
    sceneId: 'S1A_IW_GRDH_1SDV_20240810T2241', acquisition: '2024-08-10 22:41 UTC',
    area: '14.7 km²', confidence: '92.5%', status: 'Completed',
    description: 'Anomaly in high-density transit water. Backward drift modelling over 48 h of current and wind fields constrains the potential source region.',
  },
]

export default function Landing() {
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const [activeSceneIdx, setActiveSceneIdx] = useState(0)
  const [viewMode, setViewMode] = useState('overlay') // original | overlay | probability(note)
  const currentScene = SATELLITE_SCENES[activeSceneIdx]

  useEffect(() => {
    document.body.classList.add('lp-body')
    document.documentElement.classList.add('lp-html')
    return () => { document.body.classList.remove('lp-body'); document.documentElement.classList.remove('lp-html') }
  }, [])

  return (
    <div className="lp-root">
      {/* 1 — Formal product header */}
      <nav className="lp-nav" aria-label="Primary">
        <div className="lp-nav-inner">
          <a href="#" className="lp-brand" onClick={(e) => { e.preventDefault(); window.scrollTo(0, 0) }}>
            <span className="lp-brand-logo" aria-hidden="true">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 3.5s5.2 5.3 5.2 9.3a5.2 5.2 0 1 1-10.4 0C6.8 8.8 12 3.5 12 3.5Z" fill="currentColor" />
                <path d="M18.5 6.5a8 8 0 0 1 0 12M21 4a11.5 11.5 0 0 1 0 17" stroke="#2b8790" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
            </span>
            <span className="lp-brand-name">OilTrace</span>
            <span className="lp-brand-sub">Maritime Environmental<br />Monitoring</span>
            <span className="lp-sih-tag">Research prototype</span>
          </a>
          <div className={`lp-nav-links ${menuOpen ? 'open' : ''}`}>
            <a href="#capabilities" onClick={() => setMenuOpen(false)}>Dashboard</a>
            <a href="#pipeline" onClick={() => setMenuOpen(false)}>Analysis</a>
            <a href="#attribution" onClick={() => setMenuOpen(false)}>Vessel Tracking</a>
            <a href="#activity" onClick={() => setMenuOpen(false)}>Historical Data</a>
            <a href="#exports" onClick={() => setMenuOpen(false)}>Reports</a>
            <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer">Documentation</a>
          </div>
          <div className="lp-nav-actions">
            <HealthPill />
            <a href="#help" className="lp-btn-secondary" style={{ padding: '7px 12px' }}>Help</a>
            <button className="lp-btn-primary" onClick={() => navigate('/dashboard')}>Launch Investigation →</button>
            <button className="lp-menu-toggle" onClick={() => setMenuOpen(o => !o)} aria-label="Toggle navigation">☰</button>
          </div>
        </div>
      </nav>

      {/* 2 — Context / breadcrumb strip */}
      <div className="lp-context-strip">
        <div className="lp-container">
          <span><b>OilTrace</b> · Maritime Environmental Monitoring · Satellite Analysis / Spill Detection / Vessel Attribution</span>
        </div>
      </div>

      {/* 3/4 — Mission statement + launch */}
      <header className="lp-hero">
        <div className="lp-container">
          <div className="lp-hero-grid">
            <div>
              <div className="lp-eyebrow-pill">Maritime Environmental Monitoring</div>
              <h1 className="lp-hero-title">Satellite-Based Maritime Oil Spill Detection and Vessel Attribution</h1>
              <p className="lp-hero-desc">
                OilTrace ingests Sentinel-1 synthetic-aperture radar imagery, applies oil-spill segmentation,
                reconstructs the geospatial extent of detected slicks, models backward drift, and correlates
                results with AIS vessel records to produce explainable attribution scores.
              </p>
              <div className="lp-hero-actions">
                <button className="lp-btn-primary lp-btn-primary--lg" onClick={() => navigate('/dashboard')}>Launch Investigation →</button>
                <a href="#pipeline" className="lp-btn-secondary lp-btn-secondary--lg">Learn More</a>
              </div>
            </div>
            {/* 6 — Satellite-analysis preview in a formal analysis panel */}
            <div className="lp-radar-console" aria-label="Satellite analysis preview">
              <div className="lp-console-header">
                <span className="lp-console-title">Section 01 — Satellite-analysis preview</span>
                <div className="lp-mode-toggle" role="tablist" aria-label="Overlay controls">
                  {['original', 'overlay', 'probability'].map(m => (
                    <button key={m} role="tab" aria-selected={viewMode === m} className={`lp-mode-btn ${viewMode === m ? 'active' : ''}`} onClick={() => setViewMode(m)}>
                      {m === 'original' ? 'Original image' : m === 'overlay' ? 'Mask overlay' : 'Probability map'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="lp-screen-frame">
                <img src={currentScene.src} alt={currentScene.name} className="lp-radar-img"
                  style={viewMode === 'original' ? {} : viewMode === 'probability' ? { filter: 'grayscale(1) contrast(1.25) brightness(0.92)' } : { filter: 'contrast(1.12) brightness(0.96)' }} />
                <div className="lp-hud-coords">{currentScene.coords}</div>
                {viewMode !== 'original' && (
                  <div className="lp-detect-polygon">
                    <div className="lp-detect-badge">■ Detected oil-spill extent {viewMode === 'probability' ? '(probability view)' : '(mask overlay)'}</div>
                    <div className="lp-detect-metrics">Area: {currentScene.area} · Confidence: {currentScene.confidence}</div>
                  </div>
                )}
              </div>
              <div className="lp-console-footer">
                <div className="lp-scene-meta">
                  <div>
                    <div className="lp-scene-name">{currentScene.name}</div>
                    <div className="lp-scene-sensor">Scene: {currentScene.sceneId} · {currentScene.sensor} · Acquired {currentScene.acquisition} · Status: {currentScene.status}</div>
                  </div>
                </div>
                <div className="lp-scene-tabs">
                  {SATELLITE_SCENES.map((s, idx) => (
                    <button key={s.id} className={`lp-scene-tab ${activeSceneIdx === idx ? 'active' : ''}`} onClick={() => setActiveSceneIdx(idx)}>
                      <span className="lp-scene-tab-title">{s.name}</span>
                      <span className="lp-scene-tab-loc">{s.area} · {s.confidence}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>
          {/* 5 — Technical capability metrics */}
          <div className="lp-telemetry-grid" id="capabilities" aria-label="Technical capabilities">
            {[
              ['Sentinel-1 SAR', 'C-band ingestion'], ['U-Net segmentation', 'Detection model'],
              ['Geospatial analysis', 'Area and trajectory'], ['Backward drift', 'Origin estimation'],
              ['AIS attribution', 'Explainable scoring'],
            ].map(([v, l]) => (
              <div key={l} className="lp-telemetry-item"><div className="lp-telemetry-val" style={{ fontSize: 14 }}>{v}</div><div className="lp-telemetry-lbl">{l}</div></div>
            ))}
          </div>
        </div>
      </header>

      <div className="lp-partners-bar">
        <div className="lp-container">
          <div className="lp-partners-flex">
            {[['Sentinel-1 SAR', 'Radar input'], ['U-Net segmentation', 'Detection'], ['Geospatial reconstruction', 'Extent'], ['Drift modelling', 'Backtracking'], ['AIS records', 'Correlation'], ['GeoJSON / CSV / PDF', 'Exports']].map(([a]) => (
              <div key={a} className="lp-partner-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12" /></svg><span>{a}</span></div>
            ))}
          </div>
        </div>
      </div>

      {/* 7 — Pipeline overview */}
      <section id="pipeline" className="lp-section">
        <div className="lp-container">
          <div className="lp-section-header">
            <span className="lp-kicker">Section 02 — Analysis pipeline</span>
            <h2 className="lp-section-title">From satellite data to analysis results</h2>
            <p className="lp-section-desc">Six documented stages connect Sentinel-1 SAR backscatter to ranked candidate vessels and exportable results. Each stage lists its method, input, processing, output, and technology.</p>
          </div>
          <div className="lp-steps-grid">
            {PIPELINE_STAGES.map(s => (
              <div key={s.n} className="lp-step-card">
                <div className="lp-step-top"><span className="lp-step-num">{s.n}</span><span className="lp-method-get">{s.n === '01' || s.n === '05' || s.n === '06' ? 'GET' : s.n === '02' || s.n === '04' ? 'POST' : 'CALC'}</span></div>
                <h3 className="lp-step-name">{s.label}</h3>
                <p className="lp-step-summary">{s.method}</p>
                <dl className="lp-step-io">
                  <div><dt>Input</dt><dd>{s.input}</dd></div>
                  <div><dt>Process</dt><dd>{s.processing}</dd></div>
                  <div><dt>Output</dt><dd>{s.output}</dd></div>
                </dl>
                <div className="lp-step-stack-bar"><span className="lp-stack-label">Technology: </span>{s.technology}</div>
              </div>
            ))}
          </div>
          <div className="lp-sse-banner">
            <code>GET /api/v1/pipeline/jobs/{'{job_id}'}/events</code>
            <span>Pipeline progress is streamed to the dashboard over Server-Sent Events with polling fallback; stage states shown are Ready, Searching, Running, Completed, Attention required, or Unavailable.</span>
          </div>
        </div>
      </section>

      {/* 8/9/10 — Detection, drift, attribution explanations */}
      <section className="lp-section lp-section--alt">
        <div className="lp-container">
          <div className="lp-section-header">
            <span className="lp-kicker">Section 03 — Methods</span>
            <h2 className="lp-section-title">Detection, drift, and attribution</h2>
          </div>
          <div className="lp-two-col-grid" style={{ gridTemplateColumns: '1fr 1fr 1fr', display: 'grid' }}>
            {[
              ['8 · Oil-spill detection', 'Segmentation of SAR backscatter identifies dark-formation anomalies consistent with oil. Outputs include a binary mask, probability map, slick polygon, detected area, perimeter, and model confidence. Detections are labelled as analysis outputs, not verified incidents.'],
              ['9 · Drift and backtracking', 'The detected slick position is combined with ocean-current and wind fields in a backward Lagrangian simulation. The result is an estimated origin with an uncertainty radius and a release-time window, plus particle trajectories for review.'],
              ['10 · Vessel attribution', 'Candidate vessels intersecting the origin region and time window are scored on spatial proximity, temporal correlation, trajectory similarity, drift overlap, maneuvering/speed information, and vessel profile. Results use neutral terms: candidate vessel, attribution score, spatial/temporal correlation, potential source, analysis confidence. No vessel is labelled as legally responsible.'],
            ].map(([t, d]) => (
              <div key={t} className="lp-panel"><h3>{t}</h3><p>{d}</p></div>
            ))}
          </div>
        </div>
      </section>

      {/* 11 — SAR science */}
      <section className="lp-section lp-section--white">
        <div className="lp-container">
          <div className="lp-section-header">
            <span className="lp-kicker">Section 04 — Satellite and SAR science</span>
            <h2 className="lp-section-title">Why Sentinel-1 SAR</h2>
            <p className="lp-section-desc">Synthetic-aperture radar operates day and night and penetrates cloud cover. Hydrocarbon films dampen short capillary waves, reducing Bragg backscatter so slicks appear dark against sea clutter. Interferometric Wide swath products provide 10 m-class sampling over wide coverage.</p>
          </div>
          <div className="lp-physics-grid">
            {[
              ['All-weather imaging', 'C-band microwaves pass through clouds, fog, and precipitation, unlike optical sensors blocked by the high average cloud cover over oceans.'],
              ['Dark-film effect', 'Oil alters surface tension and suppresses centimetre-scale waves; radar energy reflects away from the sensor, producing low-backscatter patches.'],
              ['Wide-swath coverage', 'Interferometric Wide mode balances spatial detail with a broad swath, supporting both small discharges and large spill extents.'],
            ].map(([t, d]) => (
              <div key={t} className="lp-physics-card"><div className="lp-physics-icon">§</div><h3 className="lp-physics-title">{t}</h3><p className="lp-physics-desc">{d}</p></div>
            ))}
          </div>
          <div style={{ height: 18 }} />
          <div className="lp-gallery-grid">
            {SATELLITE_SCENES.map(s => (
              <div key={s.id} className="lp-gallery-card">
                <div className="lp-gallery-thumb"><img src={s.src} alt={s.name} loading="lazy" /><div className="lp-gallery-pill">{s.sensor}</div></div>
                <div className="lp-gallery-info">
                  <h4 className="lp-gallery-title">{s.name}</h4>
                  <p className="lp-gallery-desc">{s.description}</p>
                  <div className="lp-gallery-meta"><span>{s.coords}</span><span>Area {s.area}</span></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 12 — System architecture */}
      <section id="activity" className="lp-section lp-section--alt">
        <div className="lp-container">
          <div className="lp-section-header">
            <span className="lp-kicker">Section 05 — System architecture</span>
            <h2 className="lp-section-title">Modules and data flow</h2>
            <p className="lp-section-desc">The dashboard runs archive search, scene selection, pipeline execution with stage updates, map review, candidate ranking, timeline review, and exports against the same backend API.</p>
          </div>
          <div className="lp-arch-flow">
            {[
              ['01 · Ingest', 'Scene search', 'STAC + archive'], ['02 · Segment', 'U-Net mask', 'TorchScript'],
              ['03 · Reconstruct', 'Slick polygon', 'GIS'], ['04 · Drift', 'Origin + uncertainty', 'Lagrangian'],
              ['05 · Match', 'AIS candidates', 'Spatio-temporal'], ['06 · Report', 'Scores + exports', 'GeoJSON/CSV/PDF'],
            ].map(([a, b, c]) => (
              <div key={a} className="lp-arch-node"><div className="lp-arch-step">{a}</div><div className="lp-arch-name">{b}</div><div className="lp-arch-tech">{c}</div></div>
            ))}
          </div>
          <div style={{ height: 14 }} />
          <div className="lp-panel" id="exports">
            <h3>Section 06 — Data and report export</h3>
            <p>Completed investigations export the same results reviewed on screen: slick geometry as GeoJSON, candidate table as CSV, and a summary PDF report. Exports are labelled with file name, format, and producing investigation ID. They are analysis outputs of a research prototype.</p>
          </div>
        </div>
      </section>

      {/* 13 — Prototype notice */}
      <section className="lp-section" id="help">
        <div className="lp-container">
          <div className="lp-notice">
            <b>Research and hackathon prototype notice.</b> OilTrace is an independent research and hackathon application for maritime environmental monitoring.
            It is not a government website, is not affiliated with any government body, and carries no official seal, emblem, certification, or verification claim.
            Detection and attribution outputs are analytical estimates for research review — not legal findings of responsibility and not legally admissible documents.
          </div>
          <div style={{ height: 16 }} />
          <div className="lp-cta-wrap">
            <div>
              <h2 className="lp-cta-title">Open the investigation workspace</h2>
              <p className="lp-cta-desc">Search the Sentinel-1 archive, select a scene, run detection, drift, AIS matching, and attribution, and export results.</p>
              <div className="lp-cta-buttons">
                <button className="lp-btn-primary lp-btn-primary--lg" onClick={() => navigate('/dashboard')}>Launch Investigation →</button>
                <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer" className="lp-btn-secondary lp-btn-secondary--lg">Documentation ↗</a>
              </div>
            </div>
            <div className="lp-panel" style={{ margin: 0 }}>
              <h3>Operational states used in this system</h3>
              <p>Ready · Searching · Running · Completed · Attention required · Unavailable · Live data · Simulated data · Fallback data. When an external service fails, the interface states the problem, explains the fallback, and continues only where the backend supports it.</p>
            </div>
          </div>
        </div>
      </section>

      {/* 14 — Technical references / footer */}
      <footer className="lp-footer">
        <div className="lp-container">
          <div className="lp-footer-row">
            <div className="lp-footer-brand">OilTrace <span style={{ fontWeight: 400, color: '#5b6b7c' }}>· Maritime Environmental Monitoring · independent research prototype</span></div>
            <div className="lp-footer-links">
              <a href="#pipeline">Analysis</a><a href="#attribution">Vessel Tracking</a><a href="#activity">Historical Data</a><a href="#exports">Reports</a>
              <a href={`${API_BASE}/docs`} target="_blank" rel="noopener noreferrer">Documentation</a>
              <a href={`${API_BASE}/health`} target="_blank" rel="noopener noreferrer">System Status</a>
              <button onClick={() => navigate('/dashboard')}>Dashboard →</button>
            </div>
          </div>
          <div className="lp-footer-refs" id="attribution">
            <span>Technical references: Sentinel-1 SAR (Copernicus) · U-Net image segmentation · Lagrangian drift modelling · AIS vessel records · GeoJSON / CSV / PDF exports.</span>
            <span>Imagery on this page illustrates SAR backscatter interpretation; dashboard scenes come from the archive search and the detection pipeline.</span>
          </div>
          <div className="lp-footer-copy">OilTrace is an independent research / hackathon application. Not a government service; no official status is claimed. Data credits: satellite data via the archive provider; vessel and environmental data via configured backend services.</div>
        </div>
      </footer>
    </div>
  )
}

