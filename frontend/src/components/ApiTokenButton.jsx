// API-token entry (#212). A key button in the header opens a small modal to
// set/clear the shared-secret token. It also auto-opens when any /api/ request
// comes back 401, so a user hitting a protected server is prompted immediately.

import { useEffect, useState } from 'react'

import { getToken, setToken, setUnauthorizedHandler } from '../api'

const backdropStyle = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.6)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
}

const modalStyle = {
  background: 'var(--bg-card, #0f1a2e)',
  border: '1px solid var(--border, #1e3a5f)',
  borderRadius: '8px',
  padding: '1.25rem',
  width: 'min(420px, 90vw)',
  color: 'var(--text-h, #f3f4f6)',
  boxShadow: '0 10px 40px rgba(0,0,0,0.5)',
}

const inputStyle = {
  width: '100%',
  boxSizing: 'border-box',
  padding: '0.5rem',
  margin: '0.75rem 0',
  background: 'var(--bg-deep, #060a10)',
  border: '1px solid var(--border, #1e3a5f)',
  borderRadius: '4px',
  color: 'var(--text-h, #f3f4f6)',
  fontFamily: 'monospace',
}

export default function ApiTokenButton() {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  const [auto, setAuto] = useState(false)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setValue(getToken())
      setAuto(true)
      setOpen(true)
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const openManual = () => {
    setValue(getToken())
    setAuto(false)
    setOpen(true)
  }

  const save = () => {
    setToken(value.trim())
    setOpen(false)
    // Reload so every view re-fetches with the new token applied.
    window.location.reload()
  }

  return (
    <>
      <button className="nav-btn" title="Set API token" onClick={openManual}>
        🔑
      </button>
      {open && (
        <div style={backdropStyle} onClick={() => setOpen(false)}>
          <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 0.5rem' }}>API Token</h3>
            <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text, #9ca3af)' }}>
              {auto
                ? 'This server requires an API token. Enter it to continue.'
                : 'Shared-secret token for this Nidozo server. Leave blank to clear.'}
            </p>
            <input
              style={inputStyle}
              type="password"
              value={value}
              placeholder="NIDOZO_API_TOKEN"
              autoFocus
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') save()
                if (e.key === 'Escape') setOpen(false)
              }}
            />
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button className="nav-btn" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button className="nav-btn active" onClick={save}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
