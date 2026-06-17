import { useTheme } from '../hooks/useTheme'

/**
 * ThemeToggle — a nav button that flips between dark and light themes.
 * Shows the icon of the theme you'd switch TO (a sun in dark mode, moon in light).
 */
export default function ThemeToggle() {
  const { theme, toggleTheme } = useTheme()
  const isDark = theme === 'dark'
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggleTheme}
      aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
      title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
    >
      {isDark ? '☀' : '☾'}
    </button>
  )
}
