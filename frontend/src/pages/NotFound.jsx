import { Link } from 'react-router-dom'
import PageShell, { Empty } from '../components/PageShell.jsx'

export default function NotFound() {
  return (
    <PageShell title="Page unavailable" crumb="Unavailable">
      <Empty>This page does not exist or the service behind it is unavailable.</Empty>
      <p><Link to="/">Back to the landing page</Link> · <Link to="/dashboard">Open the dashboard</Link></p>
    </PageShell>
  )
}
