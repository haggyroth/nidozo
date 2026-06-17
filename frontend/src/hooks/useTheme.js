import { useCallback, useEffect, useState } from 'react'

// Persisted UI theme. The app shipped dark-only (the CRT battlefield look), so
// dark stays the default; light is opt-in and remembered across sessions.
const STORAGE_KEY = 'nidozo-theme'

/** Read the persisted theme, falling back to the OS preference, then dark. */
function readInitialTheme() {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  // No stored choice — respect the OS preference, defaulting to dark.
  if (window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light'
  return 'dark'
}

/** Apply the theme to <html data-theme> so CSS variables switch synchronously. */
function applyTheme(theme) {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', theme)
  }
}

/**
 * useTheme — returns the current theme and a toggle.
 *
 * The <html data-theme> attribute is the single source of truth for CSS; an
 * inline script in index.html sets it before paint to avoid a flash, and this
 * hook keeps it in sync with React state + localStorage.
 */
export function useTheme() {
  const [theme, setTheme] = useState(readInitialTheme)

  useEffect(() => {
    applyTheme(theme)
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // localStorage can throw in private mode — theme still applies in-memory.
    }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  }, [])

  return { theme, toggleTheme }
}
