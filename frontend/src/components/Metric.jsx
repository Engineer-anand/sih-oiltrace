export default function Metric({ label, value, suffix }) {
  return <div className="metric"><span>{label}</span><strong>{value ?? '—'}{suffix && <em>{suffix}</em>}</strong></div>
}
