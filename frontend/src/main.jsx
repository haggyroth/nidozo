import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { installFetchAuth } from './api'

// Attach the API token to /api/ fetches before any component mounts (#212).
installFetchAuth()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
