// Карточка задачи: живой прогресс по SSE, степпер стадий, ETA,
// пауза/продолжить, предупреждения из QA-отчёта (сканы, память, движки),
// скачивание форматов, вычитка сегментов и пересборка файлов с правками.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { formatEta } from '../format.js'
import SegmentReview from './SegmentReview.jsx'
import StageStepper from './StageStepper.jsx'

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

  useEffect(() => {
    if (!job || (job.status !== 'running' && job.status !== 'queued')) return undefined
    const timer = setInterval(refresh, 2000)
    return () => clearInterval(timer)
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

  const download = async (format) => {
    try {
      const { blob, filename } = await api.download(jobId, format)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      onError(e.message)
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
        {job.status === 'running' && (
          <> · {Math.round(job.progress * 100)}% общий прогресс</>
        )}
        {job.status === 'running' && formatEta(job.eta_seconds) && (
          <> · осталось {formatEta(job.eta_seconds)}</>
        )}
        {job.pause_requested && job.status === 'running' && (
          <> · ставим на паузу…</>
        )}
      </p>
      {job.error && <p className="error-text">{job.error}</p>}

      {['queued', 'running', 'paused', 'failed'].includes(job.status) && (
        <StageStepper
          plan={job.stage_plan}
          stage={job.stage}
          progress={job.progress}
          status={job.status}
        />
      )}

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

      {report.coverage_warning && (
        <p className="warn-text">⚠ Неполное покрытие: {report.coverage_warning}</p>
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
      {report.stall_warning && (
        <p className="warn-text">⏱ {report.stall_warning}</p>
      )}
      {report.stage_errors?.length > 0 && (
        <ul className="stage-errors">
          {report.stage_errors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      )}
      {report.ocr && (
        <p className="muted">
          Распознано OCR-страниц: {report.ocr.pages_transcribed}
          {report.ocr.total_pages ? ` из ${report.ocr.total_pages}` : ''}
          {report.ocr.pages_rescued?.length
            ? `, взяты из текстового слоя: ${report.ocr.pages_rescued.join(', ')}`
            : ''}
          {report.ocr.pages_empty?.length
            ? `, потеряны: ${report.ocr.pages_empty.join(', ')}`
            : ''}
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
            <button
              key={fmt}
              className="download-btn"
              onClick={() => download(fmt)}
            >
              {FORMAT_TITLES[fmt] || fmt}
            </button>
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
