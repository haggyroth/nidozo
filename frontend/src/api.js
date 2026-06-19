// Centralized API-token handling (#212).
//
// The Nidozo API optionally requires a shared-secret token (set server-side via
// NIDOZO_API_TOKEN). We store the user's copy in localStorage, attach it to
// same-origin /api/ fetches as a Bearer header, route 401s to a handler, and
// build WebSocket URLs with the token as a query param (browsers can't set
// headers on a WS handshake).

const TOKEN_KEY = 'nidozo_api_token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable (private mode); token simply won't persist */
  }
}

let _onUnauthorized = null
export function setUnauthorizedHandler(fn) {
  _onUnauthorized = fn
}

function isApiPath(url) {
  return typeof url === 'string' && (url.startsWith('/api/') || url === '/healthz')
}

// Patch window.fetch once so every existing inline `fetch('/api/...')` call
// picks up the token without touching ~30 call sites. Idempotent.
export function installFetchAuth() {
  if (window.__nidozoFetchPatched) return
  const orig = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input && input.url
    if (isApiPath(url)) {
      const token = getToken()
      if (token) {
        init = {
          ...init,
          headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
        }
      }
    }
    const res = await orig(input, init)
    if (res.status === 401 && isApiPath(url) && _onUnauthorized) _onUnauthorized()
    return res
  }
  window.__nidozoFetchPatched = true
}

// Build a same-host ws:// or wss:// URL with the token appended when set.
export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken()
  const sep = path.includes('?') ? '&' : '?'
  const q = token ? `${sep}token=${encodeURIComponent(token)}` : ''
  return `${proto}://${location.host}${path}${q}`
}
