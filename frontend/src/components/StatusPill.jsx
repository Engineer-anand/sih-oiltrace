export default function StatusPill({ ok, warn, bad, info, children }) {
  const cls = bad ? 'bad' : warn ? 'warn' : info ? 'info' : ok ? 'ok' : 'warn'
  return <span className={`status-pill ${cls}`}><i />{children}</span>
}
