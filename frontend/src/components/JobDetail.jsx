import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import SegmentReview from './SegmentReview.jsx'

const FORMAT_TITLES = {
  md: 'Markdown',
  html: 'HTML',
  docx: 'DOCX',
  pdf: 'PDF',
  latex: 'LaTeX (.tex)',
  bilingual: 'Двуязычный MD',
  report: 'QA-отчёт (JSON)',
}

export default function JobDetail({ jobId, onClose, onError }) {
  const [job, setJob] = useState(null)
  const [showReview, setShowReview] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)
  const [pausing, setPausing] = useState(false)
  const [resuming, setResuming] = useState(false)

  const refresh = useCallback(() => {
    api.job(jobId).then(setJob).catch((e) => onError(e.message))
  }, [jobId, onError])

  useEffect(() => {
    refresh()
  }, [refresh])

  // live progress: SSE stream with polling as a fallback
  useEffect(() => {
    if (!job || (job.status !== 'running' && job.status !== 'queued')) return undefined
    let timer = null
    let source = null
    if (typeof EventSource !== 'undefined') {
      source = new EventSource(`/api/jobs/${jobId}/events/`)
      source.onmessage = (event) => {
        const data = JSON.parse(event.data)
        setJob((prev) => (prev ? { ...prev, ...data } : prev))
        if (['completed', 'partial', 'failed', 'paused'].includes(data.status)) {
          source.close()
          refresh() // pull the full record with report and formats
        }
      }
      source.onerror = () => {
        source.close()
        timer = setInterval(refresh, 2000)
      }
    } else {
      timer = setInterval(refresh, 2000)
    }
    return () => {
      if (source) source.close()
      if (timer) clearInterval(timer)
    }
  }, [job?.status, jobId, refresh])

  const rebuild = async () => {
    setRebuilding(true)
    try {
      await api.rebuild(jobId)
      refresh()
    } catch (e) {
      onError(e.message)
    } finally {
      setRebuilding(false)
    }
  }

  const pause = async () => {
    setPausing(true)
    try {
      await api.pauseJob(jobId)
      refresh()
    } catch (e) {
      onError(e.message)
    } finally {
      setPausing(false)
    }
  }

  const resume = async () => {
    setResuming(true)
    try {
      await api.resumeJob(jobId)
      refresh()
    } catch (e) {
      onError(e.message)
    } finally {
      setResuming(false)
    }
  }

  if (!job) return <p className="muted">Загрузка…</p>

  const report = job.report || {}
  return (
    <div>
      <div className="detail-header">
        <h2>{job.name || job.id}</h2>
        <button className="ghost" onClick={onClose}>✕</button>
      </div>

      <p>
        Статус: <b>{job.status}</b>
        {job.stage && job.status === 'running' && (
          <> · {job.stage} {Math.round(job.progress * 100)}%</>
        )}
        {job.pause_requested && job.status === 'running' && (
          <> · ставим на паузу…</>
        )}
      </p>
      {job.error && <p className="error-text">{job.error}</p>}

      {(job.status === 'running' || job.status === 'queued') && (
        <div className="actions">
          <button onClick={pause} disabled={pausing || job.pause_requested}>
            {job.pause_requested ? 'Пауза после текущего сегмента…' : pausing ? 'Ставим на паузу…' : 'Пауза'}
          </button>
        </div>
      )}

      {job.status === 'paused' && (
        <>
          <p className="warn-text">
            ⏸ Задача на паузе{report.segments_done != null && (
              <> — переведено {report.segments_done} из {report.segments_translated} сегментов</>
            )}. Уже переведённая часть доступна для скачивания ниже.
          </p>
          <div className="actions">
            <button onClick={resume} disabled={resuming}>
              {resuming ? 'Возобновляем…' : 'Продолжить перевод'}
            </button>
          </div>
        </>
      )}

      {report.scan_warning && (
        <p className="warn-text">⚠ {report.scan_warning}</p>
      )}
      {report.language_warning && (
        <p className="warn-text">⚠ {report.language_warning}</p>
      )}
      {report.parser_fallback && (
        <p className="warn-text">⚠ {report.parser_fallback}</p>
      )}
      {report.memory_warning && (
        <p className="warn-text">🧠 {report.memory_warning}</p>
      )}
      {report.ocr && (
        <p className="muted">
          Распознано OCR-страниц: {report.ocr.pages_transcribed}
        </p>
      )}

      {report.segments_translated != null && (
        <p className="muted">
          Сегментов переведено: {report.segments_translated}, проблемных:{' '}
          {report.segments_failed}, предупреждений: {report.warnings}
          {report.parser_backend && <> · парсер: {report.parser_backend}</>}
          {report.duration_sec && <> · {report.duration_sec} c</>}
        </p>
      )}

      {job.formats?.length > 0 && (
        <div className="downloads">
          {job.formats.map((fmt) => (
            <a
              key={fmt}
              className="download-btn"
              href={api.downloadUrl(job.id, fmt)}
              target={fmt === 'html' ? '_blank' : undefined}
              rel="noreferrer"
            >
              {FORMAT_TITLES[fmt] || fmt}
            </a>
          ))}
        </div>
      )}

      {report.export_engines &&
        Object.entries(report.export_engines)
          .filter(([, v]) => typeof v === 'string' && v.startsWith('unavailable'))
          .map(([fmt, reason]) => (
            <p key={fmt} className="warn-text">
              ⚠ {(FORMAT_TITLES[fmt] || fmt)} не собран — {reason.replace('unavailable: ', '')}
            </p>
          ))}

      {(job.status === 'completed' || job.status === 'partial') && (
        <div className="actions">
          <button onClick={() => setShowReview((v) => !v)}>
            {showReview ? 'Скрыть вычитку' : 'Вычитка по сегментам'}
          </button>
          <button onClick={rebuild} disabled={rebuilding}>
            {rebuilding ? 'Пересборка…' : 'Пересобрать файлы с правками'}
          </button>
        </div>
      )}

      {showReview && <SegmentReview jobId={job.id} onError={onError} />}
    </div>
  )
}
