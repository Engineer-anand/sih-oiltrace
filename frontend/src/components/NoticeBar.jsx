import { useState } from 'react'

/**
 * Formal notice strip.
 *
 * It does two jobs at once. Visually it supplies the institutional register
 * that an official monitoring portal has (bordered, labelled, restrained).
 * Substantively it is the single clearest statement of data provenance in the
 * application, in the same register rather than buried in a tooltip.
 *
 * The text is derived from the server-reported mode, so it cannot drift out of
 * step with what the API actually returned. There is no "live" wording here
 * unless the backend really reported LIVE.
 */

const MODE_NOTICE = {
  LIVE: {
    tone: 'ok',
    title: 'Live data',
    body: 'All pipeline stages are serving provider data within configured freshness thresholds.',
  },
  ARCHIVE: {
    tone: 'info',
    title: 'Archived data',
    body: 'Provider data is being served from cache. Field age is reported with each stage result.',
  },
  SCENARIO: {
    tone: 'warn',
    title: 'Demonstration mode',
    body: 'The drift and AIS stages use labelled scenario data. Detection and geospatial stages run real model inference on the supplied scene.',
  },
  FALLBACK: {
    tone: 'warn',
    title: 'Substitute data',
    body: 'A real source failed and a substitute was used. This is permitted in development only and is labelled on every result.',
  },
  DEGRADED: {
    tone: 'warn',
    title: 'Degraded result',
    body: 'Real data was used but a quality gate did not pass. Warnings are attached to the affected stage.',
  },
  FAILED: {
    tone: 'bad',
    title: 'Data unavailable',
    body: 'Required source data was unavailable. No output has been fabricated for the affected stage.',
  },
}

export default function NoticeBar({ mode }) {
  const [open, setOpen] = useState(true)
  if (!open) return null

  const notice = MODE_NOTICE[mode] || {
    tone: 'info',
    title: 'Provenance pending',
    body: 'Run an investigation to see the data provenance reported for each pipeline stage.',
  }

  return (
    <div className={`ot-notice ot-notice-${notice.tone}`} role="status" aria-live="polite">
      <span className="ot-notice-tag">Notice</span>
      <span className="ot-notice-text">
        <b>{notice.title}.</b> {notice.body}
      </span>
      <span className="ot-notice-ref mono">{mode ? `MODE:${mode}` : 'MODE:—'}</span>
      <button
        type="button"
        className="ot-notice-close"
        aria-label="Dismiss notice"
        onClick={() => setOpen(false)}
      >
        ×
      </button>
    </div>
  )
}
