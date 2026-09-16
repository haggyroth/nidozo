// Centralized API-token handling (#212, #276).
//
// The Nidozo API optionally requires a shared-secret token (set server-side via
// NIDOZO_API_TOKEN). We store the user's copy in sessionStorage — not
// localStorage (#276): the token is a bearer credential for an API that spends
// real LLM credits, so it shouldn't sit on disk or be shared with every tab for
// the lifetime of the browser profile. It is attached to same-origin /api/
// fetches as a Bearer header, 401s are routed to a handler, and WebSocket
// handshakes carry it in the Sec-WebSocket-Protocol header rather than the URL,
// where it would land in the server's access log.

const TOKEN_KEY = 'nidozo_api_token'

// Sec-WebSocket-Protocol entry carrying the credential. The token is base64url
// encoded because a subprotocol value must match RFC 6455's token grammar — a
// raw base64 secret (with `+`, `/`, `=`) would make the browser throw before the
// socket ever opened.
const WS_SUBPROTOCOL_PREFIX = 'nidozo-auth.'

let _migratedLegacy = false

// Older builds kept the token in localStorage. Move it into sessionStorage once
// and delete the long-lived copy, so upgrading doesn't log the user out while
// also shrinking where the secret lives.
function migrateLegacyToken() {
  if (_migratedLegacy) return
  _migratedLegacy = true
  try {
    const legacy = localStorage.getItem(TOKEN_KEY)
    if (!legacy) return
    if (!sessionStorage.getItem(TOKEN_KEY)) sessionStorage.setItem(TOKEN_KEY, legacy)
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable (private mode) — nothing to migrate */
  }
}

export function getToken() {
  migrateLegacyToken()
  try {
    return sessionStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
    // Never leave a copy behind in the long-lived store.
    localStorage.removeItem(TOKEN_KEY)
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

// Same-host ws:// or wss:// URL. Deliberately carries no credential — see
// wsProtocols() for that.
export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}`
}

function encodeToken(token) {
  try {
    const bytes = new TextEncoder().encode(token)
    let binary = ''
    for (const byte of bytes) binary += String.fromCharCode(byte)
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  } catch {
    return ''
  }
}

// Subprotocols to offer on a WebSocket handshake. Browsers can set this header,
// unlike Authorization, and it never appears in the access log (#276).
export function wsProtocols() {
  const token = getToken()
  const encoded = token ? encodeToken(token) : ''
  return encoded ? [`${WS_SUBPROTOCOL_PREFIX}${encoded}`] : []
}
