import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import UploadForm from './components/UploadForm.jsx'
import JobList from './components/JobList.jsx'
import JobDetail from './components/JobDetail.jsx'

export default function App() {
  const [meta, setMeta] = useState(null)
  const [jobs, setJobs] = useState([])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)

  const refreshJobs = useCallback(async () => {
    try {
      const data = await api.jobs()
      setJobs(data.jobs)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    api.providers().then(setMeta).catch((e) => setError(e.message))
    refreshJobs()
  }, [refreshJobs])

  // poll while any job is active
  useEffect(() => {
    const active = jobs.some((j) => j.status === 'queued' || j.status === 'running')
    if (!active) return undefined
    const timer = setInterval(refreshJobs, 2000)
    return () => clearInterval(timer)
  }, [jobs, refreshJobs])

  return (
    <div className="app">
      <header>
        <h1>pdftransl</h1>
        <p className="tagline">
          Перевод научных PDF: формулы, таблицы и рисунки остаются на месте
        </p>
      </header>

      {error && (
        <div className="error-banner" onClick={() => setError(null)}>
          {error} ✕
        </div>
      )}

      <main>
        <section className="panel">
          <h2>Новый перевод</h2>
          <UploadForm
            meta={meta}
            onSubmitted={() => refreshJobs()}
            onError={setError}
          />
        </section>

        <section className="panel">
          <h2>Задачи</h2>
          <JobList jobs={jobs} selectedId={selected} onSelect={setSelected} />
        </section>

        {selected && (
          <section className="panel wide">
            <JobDetail
              jobId={selected}
              onClose={() => setSelected(null)}
              onError={setError}
            />
          </section>
        )}
      </main>

      <footer>
        <TmBadge />
      </footer>
    </div>
  )
}

function TmBadge() {
  const [stats, setStats] = useState(null)
  useEffect(() => {
    api.tmStats().then(setStats).catch(() => {})
  }, [])
  if (!stats) return null
  return (
    <span className="tm-badge">
      Память переводов: {stats.segments} сегментов, правок человека:{' '}
      {stats.human_corrections}
    </span>
  )
}
