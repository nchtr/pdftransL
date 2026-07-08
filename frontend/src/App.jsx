// Каркас приложения: вкладки Перевод/Настройки/Глоссарий.
// Список задач обновляется живым SSE-стримом (/api/jobs/events/) —
// одно постоянное соединение вместо опроса сервера по таймеру;
// при ошибке стрима — фолбэк на поллинг.
import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import UploadForm from './components/UploadForm.jsx'
import JobList from './components/JobList.jsx'
import JobDetail from './components/JobDetail.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'
import GlossaryPanel from './components/GlossaryPanel.jsx'

const TABS = [
  ['translate', 'Перевод'],
  ['settings', 'Настройки'],
  ['glossary', 'Глоссарий'],
]

export default function App() {
  const [tab, setTab] = useState('translate')
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

  // live job list: one persistent SSE connection instead of polling
  // /api/jobs/ on an interval. Falls back to polling only if the browser
  // has no EventSource or the stream errors out.
  useEffect(() => {
    let timer = null
    let source = null
    if (typeof EventSource !== 'undefined') {
      source = new EventSource('/api/jobs/events/')
      source.onmessage = (event) => {
        setJobs(JSON.parse(event.data).jobs)
      }
      source.onerror = () => {
        source.close()
        timer = setInterval(refreshJobs, 2000)
      }
    } else {
      timer = setInterval(refreshJobs, 2000)
    }
    return () => {
      if (source) source.close()
      if (timer) clearInterval(timer)
    }
  }, [refreshJobs])

  const deleteJob = async (id) => {
    if (!confirm('Удалить задачу вместе с файлами результата?')) return
    try {
      await api.deleteJob(id)
      if (selected === id) setSelected(null)
      refreshJobs()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="app">
      <header>
        <h1>pdftransl</h1>
        <p className="tagline">
          Перевод научных PDF: формулы, таблицы и рисунки остаются на месте
        </p>
        <nav className="tabs">
          {TABS.map(([key, title]) => (
            <button
              key={key}
              className={'tab' + (tab === key ? ' active' : '')}
              onClick={() => setTab(key)}
            >
              {title}
            </button>
          ))}
        </nav>
      </header>

      {error && (
        <div className="error-banner" onClick={() => setError(null)}>
          {error} ✕
        </div>
      )}

      {tab === 'translate' && (
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
            <JobList
              jobs={jobs}
              selectedId={selected}
              onSelect={setSelected}
              onDelete={deleteJob}
            />
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
      )}

      {tab === 'settings' && (
        <main>
          <section className="panel wide">
            <h2>Серверные настройки</h2>
            <SettingsPanel meta={meta} onError={setError} />
          </section>
        </main>
      )}

      {tab === 'glossary' && (
        <main>
          <section className="panel wide">
            <h2>Глоссарий</h2>
            <GlossaryPanel onError={setError} />
          </section>
        </main>
      )}

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
