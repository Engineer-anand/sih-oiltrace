import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import { MAP_STYLE } from '../lib/mapStyle.js'

function geoPoint(lon, lat) { return { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] }, properties: {} } }
function geoLine(coords) { return { type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} } }

export default function DriftAnimator({ data }) {
  const ref = useRef(null)
  const mapRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [frame, setFrame] = useState(0)
  const [speed, setSpeed] = useState(1)
  const [mapReady, setMapReady] = useState(false)
  const frames = useMemo(() => Array.isArray(data?.origin?.particle_trajectories) ? data.origin.particle_trajectories : [], [data?.origin?.particle_trajectories])
  const maxFrames = useMemo(() => {
    if (!frames.length) return 0
    const lens = frames.map(p => (Array.isArray(p) ? p.length : 0))
    return Math.max(0, Math.max(0, ...lens) - 1)
  }, [frames])

  useEffect(() => {
    if (!ref.current || mapRef.current) return
    const map = new maplibregl.Map({ container: ref.current, style: MAP_STYLE, center: [0, 20], zoom: 2 })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.on('load', () => {
      map.addSource('anim-particles', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addSource('anim-tracks', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addSource('anim-endpoints', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({ id: 'anim-tracks-line', type: 'line', source: 'anim-tracks', paint: { 'line-color': '#4a90a4', 'line-width': 1.3, 'line-opacity': 0.5 } })
      map.addLayer({ id: 'anim-points', type: 'circle', source: 'anim-particles', paint: { 'circle-radius': 3.2, 'circle-color': '#c96a1b', 'circle-opacity': 0.9 } })
      map.addLayer({ id: 'anim-endpoints', type: 'circle', source: 'anim-endpoints', paint: { 'circle-radius': 5, 'circle-color': '#0e2a47', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })
      mapRef.current = map
      setMapReady(true)
    })
    return () => { map.remove(); mapRef.current = null }
  }, [])

  useEffect(() => {
    if (!playing || maxFrames < 1) return
    const id = setInterval(() => setFrame(f => {
      if (f >= maxFrames) { setPlaying(false); return f }
      return f + 1
    }), Math.max(80, 550 / speed))
    return () => clearInterval(id)
  }, [playing, maxFrames, speed])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !map.isStyleLoaded()) return
    const pts = []
    const lines = []
    const ends = []
    for (const p of frames) {
      if (!Array.isArray(p)) continue
      const slice = p.slice(0, Math.min(frame + 1, p.length))
      if (!slice.length) continue
      const coords = slice
        .map(x => (x && Number.isFinite(Number(x.lon)) && Number.isFinite(Number(x.lat)) ? [Number(x.lon), Number(x.lat)] : null))
        .filter(Boolean)
      if (coords.length > 1) lines.push(geoLine(coords))
      const current = slice[slice.length - 1]
      if (current && Number.isFinite(Number(current.lon)) && Number.isFinite(Number(current.lat))) {
        pts.push(geoPoint(Number(current.lon), Number(current.lat)))
      }
      const end = p[p.length - 1]
      if (end && Number.isFinite(Number(end.lon)) && Number.isFinite(Number(end.lat))) {
        ends.push(geoPoint(Number(end.lon), Number(end.lat)))
      }
    }
    map.getSource('anim-particles')?.setData({ type: 'FeatureCollection', features: pts })
    map.getSource('anim-tracks')?.setData({ type: 'FeatureCollection', features: lines })
    map.getSource('anim-endpoints')?.setData({ type: 'FeatureCollection', features: ends })
  }, [frames, frame, mapReady])

  useEffect(() => {
    const map = mapRef.current
    const first = frames.flat().filter(Boolean)
    if (!map || !mapReady || !first.length || !map.isStyleLoaded()) return
    const xs = first.map(x => Number(x.lon)).filter(Number.isFinite)
    const ys = first.map(x => Number(x.lat)).filter(Number.isFinite)
    if (!xs.length || !ys.length) return
    const minX = Math.min(...xs), maxX = Math.max(...xs)
    const minY = Math.min(...ys), maxY = Math.max(...ys)
    try {
      if (minX === maxX && minY === maxY) {
        map.flyTo({ center: [minX, minY], zoom: 8, duration: 600 })
      } else {
        const bounds = new maplibregl.LngLatBounds([minX, minY], [maxX, maxY])
        map.fitBounds(bounds, { padding: 50, duration: 700, maxZoom: 8 })
      }
    } catch (err) {
      console.warn('DriftAnimator fitBounds failed:', err)
    }
  }, [frames, mapReady])

  if (!frames.length) return <div className="empty-panel">No trajectory history is available. Run the backward drift stage with OpenDrift to generate the animation.</div>
  const sample = frames[0]?.[Math.min(frame, frames[0].length - 1)]
  return <div className="anim-wrap">
    <div ref={ref} className="anim-map" />
    <div className="anim-controls">
      <button className="primary" onClick={() => { if (frame >= maxFrames) setFrame(0); setPlaying(v => !v) }}>{playing ? 'Pause' : 'Play backtracking'}</button>
      <button onClick={() => setFrame(0)}>Reset</button>
      <input aria-label="Animation progress" type="range" min="0" max={Math.max(0, maxFrames)} value={frame} onChange={e => setFrame(Number(e.target.value))} />
      <select value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value="0.5">0.5×</option><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select>
      <span>{sample?.time ? new Date(sample.time).toLocaleString() : '—'}</span>
    </div>
    <div className="anim-legend"><span><i className="dot teal" />particle path</span><span><i className="dot amber" />current particle</span><span><i className="dot red" />probable origin endpoint</span></div>
  </div>
}
