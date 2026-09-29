import { useEffect, useRef } from 'react'
import maplibregl from 'maplibre-gl'
import { MAP_STYLE } from '../lib/mapStyle.js'

function collection(features) {
  return { type: 'FeatureCollection', features }
}

export default function TrackingMap({ history }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const positions = history?.positions || []

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new maplibregl.Map({ container: containerRef.current, style: MAP_STYLE, center: [0, 20], zoom: 2 })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.on('load', () => {
      map.addSource('vessel-history-line', { type: 'geojson', data: collection([]) })
      map.addSource('vessel-history-points', { type: 'geojson', data: collection([]) })
      map.addSource('vessel-history-latest', { type: 'geojson', data: collection([]) })
      map.addLayer({ id: 'vessel-history-line-layer', type: 'line', source: 'vessel-history-line', paint: { 'line-color': '#d26b2d', 'line-width': 3, 'line-opacity': 0.9 } })
      map.addLayer({ id: 'vessel-history-points-layer', type: 'circle', source: 'vessel-history-points', paint: { 'circle-radius': 3.5, 'circle-color': '#2f7380', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } })
      map.addLayer({ id: 'vessel-history-latest-layer', type: 'circle', source: 'vessel-history-latest', paint: { 'circle-radius': 7, 'circle-color': '#c4473b', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } })
      mapRef.current = map
      map.fire('history-ready')
    })
    return () => { map.remove(); mapRef.current = null }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    const valid = positions.filter(p => Number.isFinite(Number(p.lon)) && Number.isFinite(Number(p.lat)))
    const coords = valid.map(p => [Number(p.lon), Number(p.lat)])
    const points = valid.map(p => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [Number(p.lon), Number(p.lat)] }, properties: { time: p.ts || '' } }))
    const latest = valid.length ? [points[points.length - 1]] : []
    map.getSource('vessel-history-line')?.setData(collection(coords.length > 1 ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: coords }, properties: {} }] : []))
    map.getSource('vessel-history-points')?.setData(collection(points))
    map.getSource('vessel-history-latest')?.setData(collection(latest))
    if (coords.length > 1) {
      const bounds = coords.reduce((b, coord) => b.extend(coord), new maplibregl.LngLatBounds(coords[0], coords[0]))
      map.fitBounds(bounds, { padding: 55, maxZoom: 10, duration: 500 })
    }
  }, [positions])

  if (!positions.length) return null
  return (
    <section className="tracking-map-panel" aria-label="Vessel track map">
      <div className="section-head"><div className="dash-card-title"><span className="section-num">M</span><b>Vessel track map</b></div><span>{history.synthetic ? 'Simulated track' : 'AIS history'}</span></div>
      <div ref={containerRef} className="tracking-map" />
      <div className="tracking-map-legend"><span><i className="dot teal" />position fixes</span><span><i className="dot amber" />track line</span><span><i className="dot red" />latest position</span></div>
    </section>
  )
}
