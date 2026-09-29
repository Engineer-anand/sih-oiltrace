// Configurable basemap. Public MapLibre raster/vector tiles
// are suitable for testing, not a production SLA — the original OilTrace build used
// it directly in two places, which is exactly the kind of thing that can go
// blank in front of judges on conference wifi. Set VITE_MAP_STYLE to any
// full MapLibre style.json URL (MapTiler, Stadia, self-hosted, etc.) for
// production; otherwise we fall back to a plain OSM raster style, which has
// no external style.json dependency and only needs tile.osm.org to be up.
const OSM_RASTER_FALLBACK = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: [
        'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
        'https://c.tile.openstreetmap.org/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
  glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
}

export const MAP_STYLE = import.meta.env.VITE_MAP_STYLE || OSM_RASTER_FALLBACK
