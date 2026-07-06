import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

const PAGE = 20

export default function SegmentReview({ jobId, onError }) {
  const [segments, setSegments] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [editing, setEditing] = useState(null) // order
  const [draft, setDraft] = useState('')

  const load = useCallback(() => {
    api
      .segments(jobId, { offset, limit: PAGE, flagged: flaggedOnly })
      .then((data) => {
        setSegments(data.segments)
        setTotal(data.total)
      })
      .catch((e) => onError(e.message))
  }, [jobId, offset, flaggedOnly, onError])

  useEffect(() => {
    load()
  }, [load])

  const save = async (order) => {
    try {
      await api.correct(jobId, order, draft)
      setEditing(null)
      load()
    } catch (e) {
      onError(e.message)
    }
  }

  return (
    <div className="review">
      <div className="review-controls">
        <label className="inline">
          <input
            type="checkbox"
            checked={flaggedOnly}
            onChange={(e) => {
              setOffset(0)
              setFlaggedOnly(e.target.checked)
            }}
          />
          только проблемные
        </label>
        <span className="muted">
          {offset + 1}–{Math.min(offset + PAGE, total)} из {total}
        </span>
        <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>←</button>
        <button
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
        >→</button>
      </div>

      {segments.map((seg) => (
        <div
          key={seg.order}
          className={'segment kind-' + seg.kind + (seg.ok ? '' : ' flagged')}
        >
          {seg.kind === 'translate' ? (
            <div className="segment-pair">
              <pre className="seg-source">{seg.source_text}</pre>
              {editing === seg.order ? (
                <div className="seg-edit">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={Math.min(12, draft.split('\n').length + 2)}
                  />
                  <div>
                    <button onClick={() => save(seg.order)}>Сохранить в память переводов</button>
                    <button className="ghost" onClick={() => setEditing(null)}>Отмена</button>
                  </div>
                </div>
              ) : (
                <pre
                  className="seg-target"
                  title="Нажмите, чтобы исправить"
                  onClick={() => {
                    setEditing(seg.order)
                    setDraft(seg.corrected || seg.translation || '')
                  }}
                >
                  {seg.corrected || seg.translation}
                  {seg.corrected && <span className="badge human">правка</span>}
                </pre>
              )}
            </div>
          ) : (
            <pre className="seg-pass">{seg.source_text}</pre>
          )}
          {seg.issues?.length > 0 && (
            <ul className="issues">
              {seg.issues.map((issue, i) => (
                <li key={i} className={'issue-' + issue.severity}>
                  [{issue.code}] {issue.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}
