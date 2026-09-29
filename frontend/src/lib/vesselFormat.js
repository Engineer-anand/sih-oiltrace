/**
 * Format any MMSI for UI presentation — honestly.
 * Synthetic SCENARIO-/FALLBACK- identifiers render as-is; nothing is ever
 * mapped to a plausible 9-digit number.
 */
export function formatMmsi(mmsi) {
  if (!mmsi) return '—'
  return String(mmsi).trim() || '—'
}

/**
 * Format any vessel name for UI presentation.
 * Scenario/fallback names already carry their suffix from the backend.
 */
export function formatVesselName(name, mmsi) {
  if (name) {
    const clean = String(name)
      .replace(/\s*[\(\[]?(scenario|fallback|demo)[\)\]]?/gi, '')
      .trim()
    if (clean) return clean
  }
  const id = mmsi ? String(mmsi).replace(/^SCENARIO-|^FALLBACK-/i, 'ID-').trim() : ''
  if (id) return `Vessel ${id}`
  return 'Vessel'
}
