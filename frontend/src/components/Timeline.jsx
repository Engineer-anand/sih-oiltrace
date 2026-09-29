import { formatVesselName } from '../lib/vesselFormat.js'

export default function Timeline({ data }) {
  const stages = [
    ['Detection', data?.incident?.detected_at, true],
    ['Probable origin', data?.origin?.release_time_window_start, !!data?.origin],
    ['AIS matching', data?.vessels?.length ? `${data.vessels.length} candidates` : null, !!data?.vessels],
    ['Attribution', data?.vessels?.[0] ? `Top candidate: ${formatVesselName(data.vessels[0].name, data.vessels[0].mmsi)} (${Number(data.vessels[0].total_score || 0).toFixed(1)})` : null, !!data?.vessels?.length],
  ]
  return <div className="timeline">{stages.map(([name, val, done], i) => <div className="timeline-row" key={name}><div className={`timeline-dot ${done ? 'done' : ''}`}>{i + 1}</div><div><b>{name}</b><small>{val || 'Waiting for stage output'}</small></div></div>)}</div>
}
