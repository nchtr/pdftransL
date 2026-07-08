// Per-stage progress breakdown: pdftransl computes a weighted stage plan
// for each job (pdftransl/progress.py) based on which stages its config
// actually runs, so a job with review/export disabled doesn't waste half
// the bar on steps that will never happen. This renders that plan as a
// stepper instead of one flat percentage.
const EPS = 1e-6

export default function StageStepper({ plan, stage, progress, status }) {
  if (!plan || plan.length === 0) return null

  const activeIndex = plan.findIndex((s) => s.key === stage)

  return (
    <ol className="stage-stepper">
      {plan.map((s, i) => {
        const end = s.start + s.weight
        const done = progress >= end - EPS
        const isCurrent =
          !done && (i === activeIndex || (progress > s.start + EPS && progress < end - EPS))
        const failedHere = isCurrent && status === 'failed'
        const pausedHere = isCurrent && status === 'paused'
        const active = isCurrent && !failedHere && !pausedHere && status === 'running'
        const subPct = Math.round(
          Math.min(1, Math.max(0, s.weight ? (progress - s.start) / s.weight : 0)) * 100
        )

        let cls = 'pending'
        let marker = ''
        if (done) {
          cls = 'done'
          marker = '✓'
        } else if (failedHere) {
          cls = 'failed'
          marker = '✕'
        } else if (pausedHere) {
          cls = 'paused'
          marker = '⏸'
        } else if (active) {
          cls = 'active'
        }

        return (
          <li key={s.key} className={'stage-step ' + cls}>
            <span className="stage-marker" aria-hidden="true">{marker}</span>
            <span className="stage-label">{s.label}</span>
            {(active || failedHere || pausedHere) && (
              <span className="stage-pct">{subPct}%</span>
            )}
          </li>
        )
      })}
    </ol>
  )
}
