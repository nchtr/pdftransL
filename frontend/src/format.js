// Shared display formatting helpers.

// eta_seconds comes from the backend (pdftransl.progress.estimate_eta_seconds):
// a linear extrapolation from elapsed time vs. progress so far. It's an
// approximation, not a promise — expect it to move around as the actual
// pace of parsing/translation becomes clearer.
export function formatEta(seconds) {
  if (seconds == null || seconds < 0 || !Number.isFinite(seconds)) return null
  if (seconds < 45) return 'меньше минуты'
  const totalMin = Math.round(seconds / 60)
  if (totalMin < 60) return `~${totalMin} мин`
  const hours = Math.floor(totalMin / 60)
  const min = totalMin % 60
  return min ? `~${hours} ч ${min} мин` : `~${hours} ч`
}
