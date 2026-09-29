import { Component } from 'react'
import { Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import Landing from './pages/Landing.jsx'
import Analysis from './pages/Analysis.jsx'
import Detection from './pages/Detection.jsx'
import Drift from './pages/Drift.jsx'
import Tracking from './pages/Tracking.jsx'
import Candidates from './pages/Candidates.jsx'
import History from './pages/History.jsx'
import Reports from './pages/Reports.jsx'
import Docs from './pages/Docs.jsx'
import NotFound from './pages/NotFound.jsx'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('OilTrace UI Error Boundary caught an error:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#071018',
          color: '#f0f6fc',
          fontFamily: 'Inter, system-ui, sans-serif',
          padding: 24,
          textAlign: 'center'
        }}>
          <div style={{
            background: 'rgba(23, 34, 48, 0.95)',
            border: '1px solid #dc2626',
            borderRadius: 12,
            padding: '32px 40px',
            maxWidth: 540,
            boxShadow: '0 20px 40px rgba(0,0,0,0.5)'
          }}>
            <h2 style={{ color: '#f87171', margin: '0 0 12px 0', fontSize: '1.4rem' }}>
              OilTrace UI Notice
            </h2>
            <p style={{ color: '#94a3b8', fontSize: '0.95rem', margin: '0 0 20px 0', lineHeight: 1.5 }}>
              The analysis interface encountered a runtime error:
            </p>
            <div style={{
              background: '#0d1520',
              padding: '12px 16px',
              borderRadius: 8,
              fontSize: '0.85rem',
              color: '#fca5a5',
              fontFamily: 'monospace',
              marginBottom: 24,
              overflowX: 'auto',
              textAlign: 'left'
            }}>
              {this.state.error?.message || 'Unknown runtime error'}
            </div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
              <button
                onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload() }}
                style={{
                  background: '#2563eb',
                  color: '#fff',
                  border: 'none',
                  padding: '10px 20px',
                  borderRadius: 6,
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                Reload Dashboard
              </button>
              <button
                onClick={() => { this.setState({ hasError: false, error: null }); window.location.href = '/' }}
                style={{
                  background: 'transparent',
                  color: '#94a3b8',
                  border: '1px solid #334155',
                  padding: '10px 20px',
                  borderRadius: 6,
                  cursor: 'pointer'
                }}
              >
                Return to Home
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/analysis" element={<Analysis />} />
        <Route path="/detection" element={<Detection />} />
        <Route path="/drift" element={<Drift />} />
        <Route path="/tracking" element={<Tracking />} />
        <Route path="/candidates" element={<Candidates />} />
        <Route path="/history" element={<History />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  )
}
