import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import './styles.css'

const basename = window.location.pathname === '/app' || window.location.pathname.startsWith('/app/') ? '/app' : '/'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename={basename === '/' ? undefined : basename}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
