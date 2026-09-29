import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import maplibregl from 'maplibre-gl'
import { API_BASE } from '../lib/api.js'
import { MAP_STYLE } from '../lib/mapStyle.js'
import { formatMmsi, formatVesselName } from '../lib/vesselFormat.js'
import 'maplibre-gl/dist/maplibre-gl.css'

function fc(features) {
  return { type: 'FeatureCollection', features: features.filter(Boolean) }
}

function feature(geometry, properties = {}) {
  return { type: 'Feature', geometry, properties }
}

function coord(lon, lat) {
  const x = Number(lon), y = Number(lat)
  return Number.isFinite(x) && Number.isFinite(y) && x >= -180 && x <= 180 && y >= -90 && y <= 90 ? [x, y] : null
}

function fitMapToBounds(map, points, options = {}) {
  if (!map || !Array.isArray(points) || !points.length) return
  const valid = points
    .map((p) => (Array.isArray(p) && p.length >= 2 ? coord(p[0], p[1]) : null))
    .filter(Boolean)
  if (valid.length === 0) return
  if (valid.length === 1) {
    try {
      map.flyTo({ center: valid[0], zoom: options.maxZoom || 10, duration: options.duration || 600 })
    } catch {}
    return
  }
  try {
    const bounds = valid.reduce(
      (b, pt) => b.extend(pt),
      new maplibregl.LngLatBounds(valid[0], valid[0])
    )
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 90, duration: 750, maxZoom: 11, ...options })
    }
  } catch (err) {
    console.warn('fitMapToBounds failed:', err)
  }
}

function haversineDistKm(lon1, lat1, lon2, lat2) {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLon = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function computeHeadingVector(lon, lat, headingDeg, lengthKm = 2.4) {
  if (headingDeg == null || isNaN(headingDeg)) return null
  const rad = (headingDeg * Math.PI) / 180
  const dLat = (lengthKm / 110.574) * Math.cos(rad)
  const dLon = (lengthKm / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(rad)
  return [
    [lon, lat],
    [lon + dLon, lat + dLat],
  ]
}

function getDegreesCardinal(deg) {
  if (deg == null || isNaN(deg)) return '—'
  const val = Math.round(deg % 360)
  const directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
  const idx = Math.round(val / 22.5) % 16
  return `${val}° ${directions[idx]}`
}

function getVesselRiskMeta(vessel, isSelected = false) {
  const rank = vessel.rank ?? 999
  const score = Number(vessel.total_score || 0)
  const isTop = rank === 1

  if (isTop) {
    return {
      tier: 'top',
      label: 'Rank #1 candidate',
      badge: '#1 TOP CANDIDATE',
      color: '#b3261e',
      strokeColor: isSelected ? '#0e2a47' : '#ffffff',
      strokeWidth: isSelected ? 3 : 2,
      radius: isSelected ? 11 : 9,
      glowColor: '#b3261e',
      trackWidth: isSelected ? 4.5 : 3.5,
      trackOpacity: 1.0,
    }
  }

  if (score >= 50 || rank <= 2) {
    return {
      tier: 'high',
      label: 'Score 50+',
      badge: `RANK #${rank} · SCORE 50+`,
      color: '#c96a1b',
      strokeColor: isSelected ? '#0e2a47' : '#ffffff',
      strokeWidth: isSelected ? 3.0 : 2.0,
      radius: isSelected ? 10 : 8,
      glowColor: '#c96a1b',
      trackWidth: isSelected ? 4.0 : 3.0,
      trackOpacity: 0.9,
    }
  }

  if (score >= 30) {
    return {
      tier: 'moderate',
      label: 'Moderate',
      badge: `RANK #${rank} CANDIDATE`,
      color: '#4a6fa5',
      strokeColor: isSelected ? '#0e2a47' : '#ffffff',
      strokeWidth: isSelected ? 3.0 : 2.0,
      radius: isSelected ? 9 : 7.5,
      glowColor: '#4a6fa5',
      trackWidth: isSelected ? 3.5 : 2.5,
      trackOpacity: 0.75,
    }
  }

  return {
    tier: 'low',
    label: 'Candidate',
    badge: `RANK #${rank} CANDIDATE`,
    color: '#6b7d90',
    strokeColor: isSelected ? '#0e2a47' : '#ffffff',
    strokeWidth: isSelected ? 2.5 : 1.5,
    radius: isSelected ? 8 : 6.5,
    glowColor: '#6b7d90',
    trackWidth: isSelected ? 3.0 : 2.0,
    trackOpacity: 0.6,
  }
}

function getNormalizedCorners(previewCorners, slickBbox) {
  if (previewCorners && Array.isArray(previewCorners) && previewCorners.length === 4) {
    const lons = previewCorners.map((c) => Number(c[0])).filter(Number.isFinite)
    const lats = previewCorners.map((c) => Number(c[1])).filter(Number.isFinite)
    if (lons.length === 4 && lats.length === 4) {
      const minX = Math.min(...lons), maxX = Math.max(...lons)
      const minY = Math.min(...lats), maxY = Math.max(...lats)
      if (minX < maxX && minY < maxY) {
        return [
          [minX, maxY], // Top-Left
          [maxX, maxY], // Top-Right
          [maxX, minY], // Bottom-Right
          [minX, minY], // Bottom-Left
        ]
      }
    }
  }
  if (slickBbox && Array.isArray(slickBbox) && slickBbox.length === 4) {
    const [minX, minY, maxX, maxY] = slickBbox.map(Number)
    if ([minX, minY, maxX, maxY].every(Number.isFinite) && minX < maxX && minY < maxY) {
      return [
        [minX, maxY],
        [maxX, maxY],
        [maxX, minY],
        [minX, minY],
      ]
    }
  }
  return null
}

function findCpaToOrigin(vessel, originCoords) {
  if (!vessel?.track?.length || !originCoords || originCoords.length !== 2) return null
  const [oLon, oLat] = originCoords
  let minDistance = Infinity
  let closestPoint = null
  for (const pt of vessel.track) {
    const c = coord(pt.lon, pt.lat)
    if (!c) continue
    const dist = haversineDistKm(c[0], c[1], oLon, oLat)
    if (dist < minDistance) {
      minDistance = dist
      closestPoint = { ...pt, coord: c, distanceKm: dist }
    }
  }
  return closestPoint
}

export default function MapView({
  data,
  aoi,
  selectedScene,
  onAoiChange,
  selectedVesselId,
  onSelectVessel,
}) {
  const ref = useRef(null)
  const mapRef = useRef(null)
  const clickRef = useRef([])
  const selectionModeRef = useRef(false)
  const onAoiChangeRef = useRef(onAoiChange)
  const dataRef = useRef(data)
  const selectVesselRef = useRef(null)

  const [mapFailed, setMapFailed] = useState(false)
  const [mapReady, setMapReady] = useState(false)
  const [selectionMode, setSelectionModeState] = useState(false)
  const [clickCount, setClickCount] = useState(0)
  const [vesselFilter, setVesselFilter] = useState('all') // 'all' | 'top' | 'high'
  const [activeVesselId, setActiveVesselId] = useState(selectedVesselId || null)
  const [hoveredWaypoint, setHoveredWaypoint] = useState(null)

  useEffect(() => {
    onAoiChangeRef.current = onAoiChange
  }, [onAoiChange])

  useEffect(() => {
    dataRef.current = data
  }, [data])

  const toggleSelectionMode = useCallback(() => {
    const next = !selectionModeRef.current
    selectionModeRef.current = next
    setSelectionModeState(next)
    setClickCount(0)
    if (!next) {
      clickRef.current = []
      mapRef.current?.getSource('aoi')?.setData(fc([]))
      if (mapRef.current) mapRef.current.getCanvas().style.cursor = ''
    } else {
      clickRef.current = []
      if (mapRef.current) mapRef.current.getCanvas().style.cursor = 'crosshair'
    }
  }, [])

  // Layer groups
  const [visibleLayers, setVisibleLayers] = useState({
    slick: true,
    drift: true,
    vessels: true,
    context: true,
  })

  // Showcase sub-toggles
  const [showcaseToggles, setShowcaseToggles] = useState({
    sarRaster: true,
    maskOverlay: true,
    tracks: true,
    headings: true,
    waypoints: true,
    cpa: true,
    labels: true,
  })

  // Synchronize internal selection with prop if provided
  useEffect(() => {
    if (selectedVesselId !== undefined) {
      setActiveVesselId(selectedVesselId)
    }
  }, [selectedVesselId])

  const selectVessel = useCallback(
    (id) => {
      const nextId = !id || id === activeVesselId ? null : id
      setActiveVesselId(nextId)
      onSelectVessel?.(nextId)
    },
    [activeVesselId, onSelectVessel]
  )

  useEffect(() => {
    selectVesselRef.current = selectVessel
  }, [selectVessel])

  // Map initialization
  useEffect(() => {
    if (!ref.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: ref.current,
      style: MAP_STYLE,
      center: [58, 20],
      zoom: 3,
    })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')

    map.on('error', (e) => {
      console.error('Map error', e?.error || e)
      if (
        /style/i.test(e?.error?.message || '') ||
        e?.error?.status === 401 ||
        e?.error?.status === 403
      ) {
        setMapFailed(true)
      }
    })

    map.on('load', () => {
      // ── Core Sources ──
      map.addSource('oil', { type: 'geojson', data: fc([]) })
      map.addSource('origin', { type: 'geojson', data: fc([]) })
      map.addSource('uncertainty', { type: 'geojson', data: fc([]) })
      map.addSource('particles', { type: 'geojson', data: fc([]) })
      map.addSource('drift-tracks', { type: 'geojson', data: fc([]) })
      map.addSource('centroid', { type: 'geojson', data: fc([]) })

      // ── Vessel Showcase Sources ──
      map.addSource('vessels', { type: 'geojson', data: fc([]) })
      map.addSource('tracks', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-tracks-glow', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-headings', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-waypoints', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-cpa', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-top-beacon', { type: 'geojson', data: fc([]) })
      map.addSource('vessel-selected-ring', { type: 'geojson', data: fc([]) })

      // ── Context / AOI Sources ──
      map.addSource('aoi', { type: 'geojson', data: fc([]) })
      map.addSource('scene-footprint', { type: 'geojson', data: fc([]) })
      map.addSource('satellite-image', {
        type: 'image',
        url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        coordinates: [
          [-180, 85],
          [180, 85],
          [180, -85],
          [-180, -85],
        ],
      })
      map.addSource('sar-overlay', {
        type: 'image',
        url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        coordinates: [
          [-180, 85],
          [180, 85],
          [180, -85],
          [-180, -85],
        ],
      })

      // ── Layers: Context & Satellite ──
      map.addLayer({
        id: 'satellite-image-layer',
        type: 'raster',
        source: 'satellite-image',
        paint: { 'raster-opacity': 0 },
      })
      map.addLayer({
        id: 'sar-overlay-layer',
        type: 'raster',
        source: 'sar-overlay',
        paint: { 'raster-opacity': 0 },
      })
      map.addLayer({
        id: 'scene-footprint-fill',
        type: 'fill',
        source: 'scene-footprint',
        paint: { 'fill-color': '#f2b84b', 'fill-opacity': 0.02 },
      })
      map.addLayer({
        id: 'scene-footprint-line',
        type: 'line',
        source: 'scene-footprint',
        paint: { 'line-color': '#f2b84b', 'line-width': 2.5, 'line-dasharray': [2, 2] },
      })
      map.addLayer({
        id: 'aoi-fill',
        type: 'fill',
        source: 'aoi',
        paint: { 'fill-color': '#1d5bb0', 'fill-opacity': 0.06 },
      })
      map.addLayer({
        id: 'aoi-line',
        type: 'line',
        source: 'aoi',
        paint: { 'line-color': '#1d5bb0', 'line-width': 1.5 },
      })
      map.addLayer({
        id: 'aoi-point',
        type: 'circle',
        source: 'aoi',
        paint: {
          'circle-radius': 6,
          'circle-color': '#0e2a47',
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
        },
      })

      // ── Layers: Oil & Drift ──
      map.addLayer({
        id: 'oil-fill',
        type: 'fill',
        source: 'oil',
        paint: { 'fill-color': '#dc2626', 'fill-opacity': 0.45 },
      })
      map.addLayer({
        id: 'oil-line',
        type: 'line',
        source: 'oil',
        paint: { 'line-color': '#ff3838', 'line-width': 2.5 },
      })
      map.addLayer({
        id: 'uncertainty-fill',
        type: 'fill',
        source: 'uncertainty',
        paint: { 'fill-color': '#c96a1b', 'fill-opacity': 0.12 },
      })
      map.addLayer({
        id: 'uncertainty-line',
        type: 'line',
        source: 'uncertainty',
        paint: { 'line-color': '#c96a1b', 'line-dasharray': [2, 2], 'line-width': 1.5 },
      })
      map.addLayer({
        id: 'particles',
        type: 'circle',
        source: 'particles',
        paint: { 'circle-radius': 3.5, 'circle-color': '#4a90a4', 'circle-opacity': 0.65 },
      })
      map.addLayer({
        id: 'drift-tracks',
        type: 'line',
        source: 'drift-tracks',
        paint: { 'line-color': '#4a90a4', 'line-width': 1.4, 'line-opacity': 0.6 },
      })

      // ── Layers: Vessel Showcase & Trajectories ──
      // 1. Vessel Track Underglow (wide blur for selected/top vessels)
      map.addLayer({
        id: 'vessel-tracks-glow',
        type: 'line',
        source: 'vessel-tracks-glow',
        paint: {
          'line-color': ['coalesce', ['get', 'color'], '#dc2626'],
          'line-width': 10,
          'line-opacity': 0.45,
          'line-blur': 4,
        },
      })

      // 2. Main Vessel Track lines (color & width by risk tier)
      map.addLayer({
        id: 'vessel-tracks',
        type: 'line',
        source: 'tracks',
        paint: {
          'line-color': ['coalesce', ['get', 'color'], '#dc2626'],
          'line-width': ['coalesce', ['get', 'width'], 3],
          'line-opacity': ['coalesce', ['get', 'opacity'], 0.85],
        },
      })

      // 3. CPA (Closest Point of Approach) Baseline to Origin
      map.addLayer({
        id: 'vessel-cpa-line',
        type: 'line',
        source: 'vessel-cpa',
        paint: {
          'line-color': '#b3261e',
          'line-width': 2.4,
          'line-dasharray': [3, 2],
          'line-opacity': 0.9,
        },
      })

      // 4. Directional Heading Navigation Rays
      map.addLayer({
        id: 'vessel-headings',
        type: 'line',
        source: 'vessel-headings',
        paint: {
          'line-color': ['coalesce', ['get', 'color'], '#dc2626'],
          'line-width': 2.5,
          'line-opacity': 0.9,
        },
      })

      // 5. Waypoint breadcrumb dots along tracks
      map.addLayer({
        id: 'vessel-waypoints',
        type: 'circle',
        source: 'vessel-waypoints',
        paint: {
          'circle-radius': 4,
          'circle-color': ['coalesce', ['get', 'color'], '#dc2626'],
          'circle-stroke-color': '#071018',
          'circle-stroke-width': 1.5,
          'circle-opacity': 0.9,
        },
      })

      // 6. Rank #1 candidate marker
      map.addLayer({
        id: 'vessel-top-beacon',
        type: 'circle',
        source: 'vessel-top-beacon',
        paint: {
          'circle-radius': 16,
          'circle-color': '#b3261e',
          'circle-opacity': 0.18,
          'circle-stroke-color': '#b3261e',
          'circle-stroke-width': 1.5,
          'circle-stroke-opacity': 0.7,
        },
      })

      // 7. Selected Vessel Halo Ring
      map.addLayer({
        id: 'vessel-selected-ring',
        type: 'circle',
        source: 'vessel-selected-ring',
        paint: {
          'circle-radius': 17,
          'circle-color': '#1d5bb0',
          'circle-opacity': 0.22,
          'circle-stroke-color': '#1d5bb0',
          'circle-stroke-width': 2,
          'circle-stroke-opacity': 0.9,
        },
      })

      // 8. Vessel Core Markers
      map.addLayer({
        id: 'vessels',
        type: 'circle',
        source: 'vessels',
        paint: {
          'circle-radius': ['coalesce', ['get', 'radius'], 8],
          'circle-color': ['coalesce', ['get', 'color'], '#dc2626'],
          'circle-stroke-color': ['coalesce', ['get', 'strokeColor'], '#ffffff'],
          'circle-stroke-width': ['coalesce', ['get', 'strokeWidth'], 2],
        },
      })

      // 9. Vessel Hit Target Area
      map.addLayer({
        id: 'vessel-hit',
        type: 'circle',
        source: 'vessels',
        paint: {
          'circle-radius': 20,
          'circle-color': '#ffffff',
          'circle-opacity': 0.01,
          'circle-pitch-alignment': 'map',
        },
      })

      // 10. Vessel Labels
      map.addLayer({
        id: 'vessel-labels',
        type: 'symbol',
        source: 'vessels',
        layout: {
          'text-field': ['get', 'label_text'],
          'text-size': 11,
          'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
          'text-offset': [0, 1.45],
          'text-anchor': 'top',
          'text-allow-overlap': true,
          'text-ignore-placement': true,
        },
        paint: {
          'text-color': ['coalesce', ['get', 'label_color'], '#ffffff'],
          'text-halo-color': '#071018',
          'text-halo-width': 2.5,
        },
      })

      // ── Origin & Centroid markers ──
      map.addLayer({
        id: 'origin',
        type: 'circle',
        source: 'origin',
        paint: {
          'circle-radius': 9,
          'circle-color': '#0e2a47',
          'circle-stroke-color': '#fff',
          'circle-stroke-width': 2,
        },
      })
      map.addLayer({
        id: 'centroid',
        type: 'circle',
        source: 'centroid',
        paint: {
          'circle-radius': 6.5,
          'circle-color': '#b3261e',
          'circle-stroke-color': '#fff',
          'circle-stroke-width': 1.5,
        },
      })

      // ── Interaction: Vessel Hit & Waypoints ──
      map.on('click', 'vessel-hit', (e) => {
        if (selectionModeRef.current) return // Allow AOI selection click to pass through
        const props = e.features?.[0]?.properties
        if (props?.vessel_id) selectVesselRef.current?.(props.vessel_id)
      })

      map.on('mouseenter', 'vessel-hit', () => {
        if (!selectionModeRef.current) map.getCanvas().style.cursor = 'pointer'
      })

      map.on('mouseleave', 'vessel-hit', () => {
        if (!selectionModeRef.current) map.getCanvas().style.cursor = ''
      })

      map.on('mouseenter', 'vessel-waypoints', (e) => {
        if (selectionModeRef.current) return
        map.getCanvas().style.cursor = 'crosshair'
        const props = e.features?.[0]?.properties
        if (props) {
          setHoveredWaypoint({
            ...props,
            point: { x: e.point.x, y: e.point.y },
          })
        }
      })

      map.on('mouseleave', 'vessel-waypoints', () => {
        if (!selectionModeRef.current) map.getCanvas().style.cursor = ''
        setHoveredWaypoint(null)
      })

      // Click for Origin / Centroid popups
      map.on('click', 'centroid', () => {
        if (selectionModeRef.current) return
        const curData = dataRef.current
        if (!curData?.incident?.slick?.centroid) return
        new maplibregl.Popup()
          .setLngLat(curData.incident.slick.centroid)
          .setHTML(
            `<div class="vessel-popup"><strong>Detected Slick Centroid</strong><span>${curData.incident.slick.centroid
              .map((v) => Number(v).toFixed(6))
              .join(', ')}</span></div>`
          )
          .addTo(map)
      })

      map.on('click', 'origin', () => {
        if (selectionModeRef.current) return
        const curData = dataRef.current
        if (!curData?.origin?.origin_centroid) return
        new maplibregl.Popup()
          .setLngLat(curData.origin.origin_centroid)
          .setHTML(
            `<div class="vessel-popup"><strong>Estimated Spill Release Origin</strong><span>${curData.origin.origin_centroid
              .map((v) => Number(v).toFixed(6))
              .join(', ')}</span></div>`
          )
          .addTo(map)
      })

      // Map global click for AOI selection
      map.on('click', (e) => {
        if (e.originalEvent?.target?.closest?.('.map-tools, .map-hud-bar, .map-carousel-hud, .map-showcase-bar, .map-vessel-dossier, .map-vessel-carousel, .map-hud, .maplibregl-control')) return
        if (!selectionModeRef.current) return

        const p = [Number(e.lngLat.lng.toFixed(5)), Number(e.lngLat.lat.toFixed(5))]
        if (clickRef.current.length === 0 || clickRef.current.length >= 2) {
          clickRef.current = [p]
          setClickCount(1)
          map.getSource('aoi')?.setData(fc([feature({ type: 'Point', coordinates: p })]))
        } else {
          const p1 = clickRef.current[0]
          clickRef.current = [p1, p]
          setClickCount(2)
          const [[x1, y1], [x2, y2]] = [p1, p]
          map
            .getSource('aoi')
            ?.setData(
              fc([
                feature({
                  type: 'Polygon',
                  coordinates: [
                    [
                      [x1, y1],
                      [x2, y1],
                      [x2, y2],
                      [x1, y2],
                      [x1, y1],
                    ],
                  ],
                }),
              ])
            )
          onAoiChangeRef.current?.([p1, p])
          selectionModeRef.current = false
          setSelectionModeState(false)
          map.getCanvas().style.cursor = ''
        }
      })

      mapRef.current = map
      setMapReady(true)
    })

    return () => map.remove()
  }, [])

  // Handle AOI selection mode cursor & Escape key cancellation
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = selectionMode ? 'crosshair' : ''
    if (!selectionMode && clickRef.current.length === 1) {
      clickRef.current = []
      map.getSource('aoi')?.setData(fc([]))
    }

    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && selectionModeRef.current) {
        selectionModeRef.current = false
        setSelectionModeState(false)
        setClickCount(0)
        clickRef.current = []
        map.getSource('aoi')?.setData(fc([]))
        map.getCanvas().style.cursor = ''
      }
    }

    if (selectionMode) {
      window.addEventListener('keydown', handleKeyDown)
      return () => window.removeEventListener('keydown', handleKeyDown)
    }
  }, [selectionMode])

  // Filter and process vessels
  const processedVessels = useMemo(() => {
    const all = data?.vessels || []
    if (vesselFilter === 'top') return all.filter((v) => v.rank === 1)
    if (vesselFilter === 'high') return all.filter((v) => Number(v.total_score || 0) >= 50 || v.rank <= 2)
    return all
  }, [data?.vessels, vesselFilter])

  // Top-ranked candidate vessel
  const topVessel = useMemo(() => {
    return (data?.vessels || []).find((v) => v.rank === 1) || data?.vessels?.[0] || null
  }, [data?.vessels])

  // Active Selected Vessel Object
  const selectedVesselObj = useMemo(() => {
    if (!activeVesselId) return null
    return (data?.vessels || []).find((v) => v.vessel_id === activeVesselId) || null
  }, [data?.vessels, activeVesselId])

  // Normalized score factors dictionary (supporting both array and dict shapes)
  const factorsMap = useMemo(() => {
    if (!selectedVesselObj?.factors) return {}
    if (Array.isArray(selectedVesselObj.factors)) {
      return Object.fromEntries(
        selectedVesselObj.factors.map((x) => [x.factor, Number(x.raw_score ?? x.score ?? 0)])
      )
    }
    return selectedVesselObj.factors
  }, [selectedVesselObj])

  // Supporting evidence list (supporting both vessel.evidence and top-level data.evidence)
  const vesselEvidence = useMemo(() => {
    if (!selectedVesselObj) return []
    if (Array.isArray(selectedVesselObj.evidence) && selectedVesselObj.evidence.length > 0) {
      return selectedVesselObj.evidence
    }
    const topEv = (data?.evidence || []).find((e) => e.vessel_id === selectedVesselObj.vessel_id)
    if (topEv?.checklist?.length) {
      return topEv.checklist.map((c) => ({
        factor: c.factor,
        description: c.evidence || c.description || `${c.factor}: verified`,
      }))
    }
    return []
  }, [selectedVesselObj, data?.evidence])

  // Update map data & features reactively
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !map.isStyleLoaded()) return

    // 1. Slick & Drift
    const spill = data?.incident?.slick?.geometry ? [feature(data.incident.slick.geometry)] : []
    const originCentroid = data?.origin?.origin_centroid
      ? coord(data.origin.origin_centroid[0], data.origin.origin_centroid[1])
      : null
    const origin = originCentroid ? [feature({ type: 'Point', coordinates: originCentroid })] : []
    const slickCentroid = data?.incident?.slick?.centroid
      ? coord(data.incident.slick.centroid[0], data.incident.slick.centroid[1])
      : null
    const centroid = slickCentroid
      ? [feature({ type: 'Point', coordinates: slickCentroid }, { label: 'Detected slick centroid' })]
      : []
    const particles = (data?.origin?.final_particle_positions || [])
      .map((p) => {
        const c = Array.isArray(p) && p.length >= 2 ? coord(p[0], p[1]) : null
        return c ? feature({ type: 'Point', coordinates: c }) : null
      })
      .filter(Boolean)
    const driftTracks = (data?.origin?.particle_trajectories || [])
      .map((track) => {
        const pts = (Array.isArray(track) ? track : [])
          .map((point) => (point ? coord(point.lon, point.lat) : null))
          .filter(Boolean)
        return pts.length > 1
          ? feature({
              type: 'LineString',
              coordinates: pts,
            })
          : null
      })
      .filter(Boolean)

    // 2. Vessels, Headings, Tracks, Waypoints
    const vesselPoints = []
    const trackFeatures = []
    const trackGlowFeatures = []
    const headingFeatures = []
    const waypointFeatures = []
    const topBeaconFeatures = []
    const selectedRingFeatures = []
    let cpaFeatures = []

    processedVessels.forEach((v) => {
      const isSelected = v.vessel_id === activeVesselId
      const isTop = v.rank === 1
      const meta = getVesselRiskMeta(v, isSelected)
      const last = v.track?.[v.track.length - 1]
      const lastPoint = last && coord(last.lon, last.lat)

      if (lastPoint) {
        // Core vessel marker
        vesselPoints.push(
          feature(
            { type: 'Point', coordinates: lastPoint },
            {
              vessel_id: v.vessel_id,
              mmsi: formatMmsi(v.mmsi),
              name: formatVesselName(v.name, v.mmsi),
              rank: v.rank || 0,
              score: Number(v.total_score || 0).toFixed(1),
              type: v.vessel_type || 'commercial',
              flag: v.flag || '—',
              speed: last.speed_kn == null ? '—' : `${Number(last.speed_kn).toFixed(1)} kn`,
              heading: last.heading_deg == null ? '—' : `${Number(last.heading_deg).toFixed(0)}°`,
              heading_dir: getDegreesCardinal(last.heading_deg),
              reported: last.time ? new Date(last.time).toUTCString().slice(5, 22) : '—',
              color: meta.color,
              radius: meta.radius,
              strokeColor: meta.strokeColor,
              strokeWidth: meta.strokeWidth,
              label_text: isTop ? `#1 ${formatVesselName(v.name, v.mmsi)}` : `${formatVesselName(v.name, v.mmsi)}`,
              label_color: isTop ? '#b3261e' : meta.color,
            }
          )
        )

        // Pulsing top beacon
        if (isTop) {
          topBeaconFeatures.push(feature({ type: 'Point', coordinates: lastPoint }))
        }

        // Selection ring
        if (isSelected) {
          selectedRingFeatures.push(feature({ type: 'Point', coordinates: lastPoint }))
        }

        // Heading vector ray
        if (showcaseToggles.headings && last.heading_deg != null) {
          const headingLine = computeHeadingVector(lastPoint[0], lastPoint[1], last.heading_deg, 2.5)
          if (headingLine) {
            headingFeatures.push(
              feature(
                { type: 'LineString', coordinates: headingLine },
                {
                  color: meta.color,
                  vessel_id: v.vessel_id,
                }
              )
            )
          }
        }
      }

      // Trajectory line
      if (showcaseToggles.tracks && v.track?.length) {
        const coords = v.track.map((p) => coord(p.lon, p.lat)).filter(Boolean)
        if (coords.length > 1) {
          const trackFeat = feature(
            { type: 'LineString', coordinates: coords },
            {
              vessel_id: v.vessel_id,
              name: formatVesselName(v.name, v.mmsi),
              color: meta.color,
              width: meta.trackWidth,
              opacity: meta.trackOpacity,
            }
          )
          trackFeatures.push(trackFeat)

          if (isSelected || isTop) {
            trackGlowFeatures.push(
              feature(
                { type: 'LineString', coordinates: coords },
                {
                  color: meta.color,
                  vessel_id: v.vessel_id,
                }
              )
            )
          }
        }
      }

      // Track Waypoints (AIS pings)
      if (showcaseToggles.waypoints && (isSelected || isTop) && v.track?.length) {
        v.track.forEach((pt, idx) => {
          const ptCoord = coord(pt.lon, pt.lat)
          if (ptCoord) {
            waypointFeatures.push(
              feature(
                { type: 'Point', coordinates: ptCoord },
                {
                  vessel_id: v.vessel_id,
                  vessel_name: formatVesselName(v.name, v.mmsi),
                  mmsi: formatMmsi(v.mmsi),
                  time: pt.time ? new Date(pt.time).toUTCString().slice(5, 22) : `Ping #${idx + 1}`,
                  speed: pt.speed_kn != null ? `${Number(pt.speed_kn).toFixed(1)} kn` : '—',
                  heading: pt.heading_deg != null ? `${Number(pt.heading_deg).toFixed(0)}°` : '—',
                  color: meta.color,
                }
              )
            )
          }
        })
      }

      // CPA Baseline between vessel track and origin centroid
      if (showcaseToggles.cpa && (isSelected || (isTop && !activeVesselId)) && originCentroid) {
        const cpa = findCpaToOrigin(v, originCentroid)
        if (cpa) {
          cpaFeatures = [
            feature(
              { type: 'LineString', coordinates: [cpa.coord, originCentroid] },
              {
                vessel_id: v.vessel_id,
                distance: `${cpa.distanceKm.toFixed(2)} km CPA`,
              }
            ),
          ]
        }
      }
    })

    // Commit to map sources
    map.getSource('oil')?.setData(fc(spill))
    map.getSource('origin')?.setData(fc(origin))
    map.getSource('centroid')?.setData(fc(centroid))
    map.getSource('particles')?.setData(fc(particles))
    map.getSource('drift-tracks')?.setData(fc(driftTracks))
    map.getSource('vessels')?.setData(fc(vesselPoints))
    map.getSource('tracks')?.setData(fc(trackFeatures))
    map.getSource('vessel-tracks-glow')?.setData(fc(trackGlowFeatures))
    map.getSource('vessel-headings')?.setData(fc(headingFeatures))
    map.getSource('vessel-waypoints')?.setData(fc(waypointFeatures))
    map.getSource('vessel-cpa')?.setData(fc(cpaFeatures))
    map.getSource('vessel-top-beacon')?.setData(fc(topBeaconFeatures))
    map.getSource('vessel-selected-ring')?.setData(fc(selectedRingFeatures))

    // Satellite Raster Overlay & Detection Mask
    const preview = data?.incident?.raster_preview
    const corners = getNormalizedCorners(preview?.corners, data?.incident?.slick?.bbox)
    const spillId = data?.spill_id || data?.incident?.incident_id

    if (spillId && corners) {
      const satelliteUrl = `${API_BASE}/api/v1/incidents/${encodeURIComponent(spillId)}/assets/normalized.png`
      const overlayUrl = `${API_BASE}/api/v1/incidents/${encodeURIComponent(spillId)}/assets/overlay.png`

      try {
        map.getSource('satellite-image')?.updateImage({ url: satelliteUrl, coordinates: corners })
        map.getSource('sar-overlay')?.updateImage({ url: overlayUrl, coordinates: corners })
        map.setPaintProperty('satellite-image-layer', 'raster-opacity', showcaseToggles.sarRaster ? 0.75 : 0)
        map.setPaintProperty('sar-overlay-layer', 'raster-opacity', showcaseToggles.maskOverlay ? 0.82 : 0)
      } catch (err) {
        console.warn('SAR raster update deferred:', err)
      }
    } else {
      try {
        map.setPaintProperty('satellite-image-layer', 'raster-opacity', 0)
        map.setPaintProperty('sar-overlay-layer', 'raster-opacity', 0)
      } catch { }
    }
  }, [data, mapReady, processedVessels, activeVesselId, showcaseToggles])

  // Synchronize Layer Group Visibility
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !map.isStyleLoaded()) return

    const groups = {
      slick: ['oil-fill', 'oil-line', 'centroid'],
      drift: ['origin', 'uncertainty-fill', 'uncertainty-line', 'particles', 'drift-tracks'],
      vessels: [
        'vessel-tracks-glow',
        'vessel-tracks',
        'vessel-cpa-line',
        'vessel-headings',
        'vessel-waypoints',
        'vessel-top-beacon',
        'vessel-selected-ring',
        'vessels',
        'vessel-hit',
        'vessel-labels',
      ],
      context: ['satellite-image-layer', 'sar-overlay-layer', 'aoi-fill', 'aoi-line', 'scene-footprint-fill', 'scene-footprint-line'],
    }

    Object.entries(groups).forEach(([group, layers]) => {
      const isVisible = visibleLayers[group]
      layers.forEach((layer) => {
        if (map.getLayer(layer)) {
          map.setLayoutProperty(layer, 'visibility', isVisible ? 'visible' : 'none')
        }
      })
    })
  }, [visibleLayers, mapReady])

  // Handle Scene Footprint
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !map.isStyleLoaded()) return
    const geometry =
      selectedScene?.geometry ||
      (selectedScene?.bbox?.length === 4
        ? {
          type: 'Polygon',
          coordinates: [
            [
              [selectedScene.bbox[0], selectedScene.bbox[1]],
              [selectedScene.bbox[2], selectedScene.bbox[1]],
              [selectedScene.bbox[2], selectedScene.bbox[3]],
              [selectedScene.bbox[0], selectedScene.bbox[3]],
              [selectedScene.bbox[0], selectedScene.bbox[1]],
            ],
          ],
        }
        : null)

    map.getSource('scene-footprint')?.setData(fc(geometry ? [feature(geometry)] : []))
    if (selectedScene?.bbox?.length === 4) {
      fitMapToBounds(
        map,
        [
          [selectedScene.bbox[0], selectedScene.bbox[1]],
          [selectedScene.bbox[2], selectedScene.bbox[3]],
        ],
        { padding: 70, duration: 500, maxZoom: 10 }
      )
    }
  }, [selectedScene, mapReady])

  // Handle AOI prop updates
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !map.isStyleLoaded()) return
    const pts = aoi || []
    clickRef.current = pts.length === 2 ? pts : []
    if (pts.length !== 2) {
      map.getSource('aoi')?.setData(fc([]))
      setSelectionModeState(false)
      selectionModeRef.current = false
      return
    }
    const [[x1, y1], [x2, y2]] = pts
    map
      .getSource('aoi')
      ?.setData(
        fc([
          feature({
            type: 'Polygon',
            coordinates: [
              [
                [x1, y1],
                [x2, y1],
                [x2, y2],
                [x1, y2],
                [x1, y1],
              ],
            ],
          }),
        ])
      )
    fitMapToBounds(map, [[x1, y1], [x2, y2]], { padding: 90, duration: 700, maxZoom: 9 })
  }, [aoi, mapReady])

  // Map camera: center on top candidate & spill
  const spotlightTopCandidate = useCallback(() => {
    const map = mapRef.current
    if (!map || !topVessel) return

    const coords = []
    if (data?.incident?.slick?.centroid) coords.push(data.incident.slick.centroid)
    if (data?.origin?.origin_centroid) coords.push(data.origin.origin_centroid)

    if (topVessel.track?.length) {
      topVessel.track.forEach((p) => {
        const c = coord(p.lon, p.lat)
        if (c) coords.push(c)
      })
    }

    selectVessel(topVessel.vessel_id)
    fitMapToBounds(map, coords, { padding: 110, duration: 900, maxZoom: 11 })
  }, [topVessel, data, selectVessel])

  // Map camera: fly to selected vessel
  const flyToVessel = useCallback(
    (vessel) => {
      const map = mapRef.current
      if (!map || !vessel) return
      const last = vessel.track?.[vessel.track.length - 1]
      const p = last && coord(last.lon, last.lat)
      if (p) {
        map.flyTo({ center: p, zoom: 10.5, duration: 800 })
      }
    },
    []
  )

  // Map camera: fit full track
  const fitVesselTrack = useCallback(
    (vessel) => {
      const map = mapRef.current
      if (!map || !vessel?.track?.length) return
      const coords = vessel.track.map((pt) => coord(pt.lon, pt.lat)).filter(Boolean)
      fitMapToBounds(map, coords, { padding: 100, duration: 750, maxZoom: 12 })
    },
    []
  )

  // Initial & reactive camera fit for investigation
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady || !data) return
    const coords = [
      ...(data?.incident?.slick?.bbox
        ? [
          [data.incident.slick.bbox[0], data.incident.slick.bbox[1]],
          [data.incident.slick.bbox[2], data.incident.slick.bbox[3]],
        ]
        : []),
      ...(data?.incident?.slick?.centroid ? [data.incident.slick.centroid] : []),
      ...(data?.origin?.origin_centroid ? [data.origin.origin_centroid] : []),
      ...((data?.vessels || []).flatMap((v) => (v.track || []).map((p) => coord(p.lon, p.lat)).filter(Boolean))),
    ]
    fitMapToBounds(map, coords, { padding: 90, duration: 750, maxZoom: 11 })
  }, [data?.spill_id, data?.vessels?.length, Boolean(data?.incident?.slick), mapReady])

  const vessels = data?.vessels || []
  const aisLive = data?.ais_source === 'gfw' && data?.using_fallback_ais === false
  const mode = data?.data_mode
  const sourceLabel =
    mode === 'SCENARIO' || data?.ais_source === 'scenario'
      ? 'Simulated Fleet'
      : mode === 'FALLBACK'
        ? 'Runtime AIS Fallback'
        : aisLive
          ? 'Global Fishing Watch'
          : data
            ? 'Runtime AIS Fallback'
            : 'Waiting for Pipeline'

  return (
    <div className="map-shell">
      <div ref={ref} className="main-map" />

      {/* ── Top Controls HUD Stack (Unified Bar + Candidate Carousel) ── */}
      {/* ── Top Controls HUD Stack ── */}
      <div className="map-top-bar-stack">
        <div className="map-hud-bar">
          <div className="hud-bar-left">
            {/* AOI Selection Button */}
            <button
              type="button"
              className={`hud-action-btn ${selectionMode ? 'active-aoi' : ''}`}
              onClick={toggleSelectionMode}
              title="Enable two-click bounding box area selection"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="4 2" />
              </svg>
              <span>
                {selectionMode
                  ? clickCount === 1
                    ? 'Click 2nd corner…'
                    : 'Click 1st corner…'
                  : 'Select AOI'}
              </span>
            </button>
            {aoi && (
              <button
                type="button"
                className="hud-action-btn clear-aoi"
                onClick={() => {
                  clickRef.current = []
                  setClickCount(0)
                  selectionModeRef.current = false
                  setSelectionModeState(false)
                  onAoiChangeRef.current?.(null)
                  mapRef.current?.getSource('aoi')?.setData(fc([]))
                  if (mapRef.current) mapRef.current.getCanvas().style.cursor = ''
                }}
                title="Clear selected area of interest"
              >
                Clear AOI ×
              </button>
            )}

            <div className="hud-v-sep" />

            {/* Sub-feature & Layer Toggles */}
            <div className="hud-toggles-group" role="group" aria-label="Map layers">
              <span className="hud-group-label">LAYERS</span>
              <button
                type="button"
                className={showcaseToggles.sarRaster ? 'active' : ''}
                onClick={() => setShowcaseToggles((t) => ({ ...t, sarRaster: !t.sarRaster }))}
                title="Toggle Satellite SAR radar raster imagery"
              >
                SAR Image
              </button>
              <button
                type="button"
                className={showcaseToggles.maskOverlay ? 'active' : ''}
                onClick={() => setShowcaseToggles((t) => ({ ...t, maskOverlay: !t.maskOverlay }))}
                title="Toggle Oil Spill Detection mask overlay"
              >
                Mask Overlay
              </button>
              <button
                type="button"
                className={showcaseToggles.headings ? 'active' : ''}
                onClick={() => setShowcaseToggles((t) => ({ ...t, headings: !t.headings }))}
                title="Toggle vessel navigation heading vectors"
              >
                Headings
              </button>
              <button
                type="button"
                className={showcaseToggles.waypoints ? 'active' : ''}
                onClick={() => setShowcaseToggles((t) => ({ ...t, waypoints: !t.waypoints }))}
                title="Toggle ping waypoints along trajectory"
              >
                Waypoints
              </button>
              <button
                type="button"
                className={showcaseToggles.cpa ? 'active' : ''}
                onClick={() => setShowcaseToggles((t) => ({ ...t, cpa: !t.cpa }))}
                title="Toggle Closest Point of Approach baseline to release origin"
              >
                CPA Line
              </button>
            </div>
          </div>

          <div className="hud-bar-right">
            {vessels.length > 0 ? (
              <>
                {topVessel && (
                  <button
                    type="button"
                    className="hud-action-btn primary"
                    onClick={spotlightTopCandidate}
                    title="Center the map on rank #1 candidate vessel and slick"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="22" y1="12" x2="18" y2="12" />
                      <line x1="6" y1="12" x2="2" y2="12" />
                      <line x1="12" y1="6" x2="12" y2="2" />
                      <line x1="12" y1="22" x2="12" y2="18" />
                    </svg>
                    <span>Focus Top Candidate</span>
                  </button>
                )}
                <div className="hud-vessel-count-badge">
                  <span className="live-radar-dot" />
                  <span>{vessels.length} VESSELS</span>
                </div>
              </>
            ) : (
              <div className="hud-status-badge">
                <span className="hud-mode-text">{sourceLabel}</span>
              </div>
            )}
          </div>
        </div>

        {/* ── Candidate Vessels Shelf (ONLY rendered when vessels exist!) ── */}
        {vessels.length > 0 && (
          <div className="map-carousel-hud">
            <div className="carousel-filter-shelf">
              <span className="carousel-lead-badge">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M2 20a2.4 2.4 0 0 0 2 1 2.4 2.4 0 0 0 2-1 2.4 2.4 0 0 1 2-1 2.4 2.4 0 0 1 2 1 2.4 2.4 0 0 0 2 1 2.4 2.4 0 0 0 2-1 2.4 2.4 0 0 1 2-1 2.4 2.4 0 0 1 2 1 2.4 2.4 0 0 0 2 1 2.4 2.4 0 0 0 2-1" />
                  <path d="M4 18L3 12h18l-1 6" />
                  <path d="M12 4v8" />
                </svg>
                <span>CANDIDATES ({vessels.length}):</span>
              </span>

              <div className="hud-segmented-filter">
                <button
                  type="button"
                  className={vesselFilter === 'all' ? 'active' : ''}
                  onClick={() => setVesselFilter('all')}
                  title="Display all tracked vessels"
                >
                  All ({vessels.length})
                </button>
                <button
                  type="button"
                  className={vesselFilter === 'top' ? 'active' : ''}
                  onClick={() => setVesselFilter('top')}
                  title="Display only the rank #1 candidate vessel"
                >
                  #1 Top
                </button>
                <button
                  type="button"
                  className={vesselFilter === 'high' ? 'active' : ''}
                  onClick={() => setVesselFilter('high')}
                  title="Filter vessels with score ≥ 50"
                >
                  Score ≥ 50
                </button>
              </div>
            </div>
            <div className="carousel-items-track">
              {vessels.map((v) => {
                const isSelected = v.vessel_id === activeVesselId
                const isTop = v.rank === 1
                const score = Number(v.total_score || 0).toFixed(1)
                const name = formatVesselName(v.name, v.mmsi)
                return (
                  <button
                    key={v.vessel_id}
                    type="button"
                    className={`vessel-chip-card ${isSelected ? 'selected' : ''} ${isTop ? 'is-top' : ''}`}
                    onClick={() => {
                      selectVessel(v.vessel_id)
                      flyToVessel(v)
                    }}
                    title={`Spotlight ${name} (Score: ${score}%)`}
                  >
                    <span className={`rank-tag ${isTop ? 'top-rank' : v.total_score >= 50 ? 'high-rank' : ''}`}>
                      #{v.rank}
                    </span>
                    <span className="vessel-chip-name">{name}</span>
                    <span className={`vessel-chip-score ${isTop ? 'top-score' : ''}`}>{score}%</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── Vessel details panel ── */}
      {selectedVesselObj && (
        <div className="map-vessel-dossier">
          <div className="dossier-header">
            <div className="dossier-tier">
              <span className={`dossier-badge ${selectedVesselObj.rank === 1 ? 'top' : ''}`}>
                {selectedVesselObj.rank === 1 ? 'RANK #1 CANDIDATE' : `RANK #${selectedVesselObj.rank} CANDIDATE`}
              </span>
              <span className="dossier-score">
                <b>{Number(selectedVesselObj.total_score || 0).toFixed(1)}</b>
                <small>/ 100</small>
              </span>
            </div>
            <button
              type="button"
              className="dossier-close"
              onClick={(e) => {
                e.stopPropagation()
                setActiveVesselId(null)
                onSelectVessel?.(null)
              }}
              aria-label="Close dossier"
              title="Close vessel details"
            >
              ×
            </button>
          </div>

          <div className="dossier-identity">
            <h4>{formatVesselName(selectedVesselObj.name, selectedVesselObj.mmsi)}</h4>
            <div className="dossier-sub">
              <span>MMSI: {formatMmsi(selectedVesselObj.mmsi)}</span>
              <span>FLAG: {selectedVesselObj.flag || '—'}</span>
              <span>TYPE: {selectedVesselObj.vessel_type || 'Tanker'}</span>
            </div>
          </div>

          {/* Telemetry Grid */}
          {(() => {
            const last = selectedVesselObj.track?.[selectedVesselObj.track.length - 1]
            const isLoitering = last?.speed_kn != null && last.speed_kn <= 3.0
            const cpa = data?.origin?.origin_centroid
              ? findCpaToOrigin(selectedVesselObj, data.origin.origin_centroid)
              : null

            return (
              <div className="dossier-telemetry">
                <div className="tele-item">
                  <label>SPEED</label>
                  <b>
                    {last?.speed_kn != null ? `${Number(last.speed_kn).toFixed(1)} kn` : '—'}
                    {isLoitering && <span className="loiter-tag">LOITER</span>}
                  </b>
                </div>
                <div className="tele-item">
                  <label>HEADING</label>
                  <b>{getDegreesCardinal(last?.heading_deg)}</b>
                </div>
                <div className="tele-item">
                  <label>CLOSEST APPROACH</label>
                  <b className="cpa-val">{cpa ? `${cpa.distanceKm.toFixed(2)} km` : '—'}</b>
                </div>
                <div className="tele-item">
                  <label>LAST PING</label>
                  <b className="time-val">
                    {last?.time ? new Date(last.time).toUTCString().slice(17, 22) + ' UTC' : '—'}
                  </b>
                </div>
              </div>
            )
          })()}

          {/* Score Factor Breakdown */}
          <div className="dossier-factors">
            <label className="factor-section-title">ATTRIBUTION FACTORS (EXPLAINABLE SCORING)</label>
            {(() => {
              const f = factorsMap || {}
              const factorList = [
                { name: 'Spatial Proximity', val: f.proximity ?? 0, weight: '25%' },
                { name: 'Temporal Window', val: f.timing ?? 0, weight: '20%' },
                { name: 'Trajectory Alignment', val: f.trajectory ?? 0, weight: '20%' },
                { name: 'Drift Plume Overlap', val: f.drift_overlap ?? 0, weight: '15%' },
                { name: 'Behavior / Speed Drop', val: f.behavior ?? 0, weight: '10%' },
                { name: 'Vessel Risk Profile', val: f.vessel_type ?? 0, weight: '10%' },
              ]

              return (
                <div className="factors-bars">
                  {factorList.map((item) => (
                    <div key={item.name} className="factor-row">
                      <div className="factor-label">
                        <span>
                          {item.name} <small>({item.weight})</small>
                        </span>
                        <b>{Number(item.val).toFixed(0)}%</b>
                      </div>
                      <div className="factor-meter">
                        <div
                          className={`factor-fill ${selectedVesselObj.rank === 1 ? 'top' : item.val >= 50 ? 'high' : ''
                            }`}
                          style={{ width: `${Math.min(100, Math.max(0, item.val))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )
            })()}
          </div>

          {/* Key Audit Trail Evidence */}
          {vesselEvidence.length > 0 && (
            <div className="dossier-evidence">
              <label>SUPPORTING EVIDENCE</label>
              <p>{vesselEvidence[0].description || vesselEvidence[0].evidence}</p>
            </div>
          )}

          {/* Map actions */}
          <div className="dossier-actions">
            <button
              type="button"
              className="action-btn"
              onClick={() => flyToVessel(selectedVesselObj)}
              title="Center camera on this vessel"
            >
              Center Ship
            </button>
            <button
              type="button"
              className="action-btn"
              onClick={() => fitVesselTrack(selectedVesselObj)}
              title="Fit map camera to full track history"
            >
              Fit Full Track
            </button>
          </div>
        </div>
      )}

      {/* ── Hover Waypoint Tooltip ── */}
      {hoveredWaypoint && (
        <div
          className="map-waypoint-tooltip"
          style={{
            left: `${hoveredWaypoint.point.x + 14}px`,
            top: `${hoveredWaypoint.point.y - 14}px`,
          }}
        >
          <strong>{hoveredWaypoint.vessel_name}</strong>
          <span>Ping: {hoveredWaypoint.time}</span>
          <div className="tooltip-metrics">
            <span>
              Speed: <b>{hoveredWaypoint.speed}</b>
            </span>
            <span>
              Course: <b>{hoveredWaypoint.heading}</b>
            </span>
          </div>
        </div>
      )}

      {/* ── Bottom-left map legend ── */}
      <div className="map-hud">
        <b>MAP LEGEND</b>
        <div className="hud-layers">
          <span>
            <i className="legend-swatch top-vessel" /> Rank #1 candidate
          </span>
          <span>
            <i className="legend-swatch high-vessel" /> Score 50+ candidate
          </span>
          <span>
            <i className="legend-swatch vessel" /> Candidate tracks
          </span>
          <span>
            <i className="legend-swatch cpa-line" /> Closest approach
          </span>
          <span>
            <i className="legend-swatch slick" /> Detected slick extent
          </span>
          <span>
            <i className="legend-swatch origin" /> Estimated origin
          </span>
          <span>
            <i className="legend-swatch drift" /> Drift particles
          </span>
        </div>

        <div className="hud-meta">
          <small className={data?.ais_source === 'scenario' ? 'fallback-source' : aisLive ? 'live-source' : 'fallback-source'}>
            AIS Source: {sourceLabel}
          </small>
          {data?.origin?.origin_centroid && (
            <small>
              Origin Centroid: {data.origin.origin_centroid.map((v) => Number(v).toFixed(5)).join(', ')}
            </small>
          )}
        </div>
      </div>

      {mapFailed && (
        <div className="map-fallback">
          Basemap tiles failed to load — check network / map style. Investigation data and AIS tracking are unaffected.
        </div>
      )}
    </div>
  )
}
