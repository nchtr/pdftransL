import { formatEta } from '../format.js'

const STATUS_LABELS = {
  queued: 'в очереди',
  running: 'выполняется',
  completed: 'готово',
  partial: 'готово (есть проблемы)',
  failed: 'ошибка',
  paused: 'на паузе',
}

export default function JobList({ jobs, selectedId, onSelect, onDelete }) {
  if (!jobs.length) return <p className="muted">Пока нет задач — загрузите PDF.</p>
  return (
    <ul className="job-list">
      {jobs.map((job) => (
        <li
          key={job.id}
          className={
            'job-item status-' + job.status + (job.id === selectedId ? ' selected' : '')
          }
          onClick={() => onSelect(job.id)}
        >
          <div className="job-title">
            <span className="job-name">{job.name || job.id}</span>
            <span className={'badge status-' + job.status}>
              {STATUS_LABELS[job.status] || job.status}
            </span>
            {onDelete && job.status !== 'running' && (
              <button
                className="ghost job-delete"
                title="Удалить задачу и файлы"
                onClick={(e) => {
                  e.stopPropagation()
                  onDelete(job.id)
                }}
              >
                ✕
              </button>
            )}
          </div>
          <div className="job-meta">
            {job.source_lang} → {job.target_lang}
            {job.status === 'running' && (
              <>
                {' · '}
                {job.stage} {Math.round(job.progress * 100)}%
                {formatEta(job.eta_seconds) && <> · осталось {formatEta(job.eta_seconds)}</>}
              </>
            )}
          </div>
          {(job.status === 'running' || job.status === 'queued') && (
            <progress value={job.progress} max="1" />
          )}
        </li>
      ))}
    </ul>
  )
}
