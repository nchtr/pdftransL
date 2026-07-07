import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

export default function GlossaryPanel({ onError }) {
  const [terms, setTerms] = useState([])
  const [term, setTerm] = useState('')
  const [translation, setTranslation] = useState('')
  const [sourceLang, setSourceLang] = useState('en')
  const [targetLang, setTargetLang] = useState('ru')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.glossary().then((d) => setTerms(d.terms)).catch((e) => onError(e.message))
  }, [onError])

  useEffect(() => {
    load()
  }, [load])

  const add = async (e) => {
    e.preventDefault()
    if (!term.trim() || !translation.trim()) return
    setBusy(true)
    try {
      await api.addTerm(term.trim(), translation.trim(), sourceLang, targetLang)
      setTerm('')
      setTranslation('')
      load()
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (row) => {
    if (!confirm(`Удалить термин «${row.term}»?`)) return
    try {
      await api.deleteTerm(row.term, row.src_lang, row.tgt_lang)
      load()
    } catch (err) {
      onError(err.message)
    }
  }

  return (
    <div>
      <p className="hint">
        📖 Термины глоссария принудительно подставляются в промпт перевода —
        так терминология остаётся единой во всех документах. Короткие правки
        из вычитки попадают сюда автоматически.
      </p>

      <form onSubmit={add} className="glossary-form">
        <input
          type="text"
          placeholder="термин (attention head)"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
        <input
          type="text"
          placeholder="перевод (головка внимания)"
          value={translation}
          onChange={(e) => setTranslation(e.target.value)}
        />
        <input
          type="text"
          className="lang-input"
          value={sourceLang}
          onChange={(e) => setSourceLang(e.target.value)}
          title="язык термина"
        />
        <span className="muted">→</span>
        <input
          type="text"
          className="lang-input"
          value={targetLang}
          onChange={(e) => setTargetLang(e.target.value)}
          title="язык перевода"
        />
        <button type="submit" disabled={busy || !term.trim() || !translation.trim()}>
          Добавить
        </button>
      </form>

      {terms.length === 0 ? (
        <p className="muted">Глоссарий пуст — добавьте первый термин.</p>
      ) : (
        <table className="glossary-table">
          <thead>
            <tr><th>Термин</th><th>Перевод</th><th>Языки</th><th>Примечание</th><th></th></tr>
          </thead>
          <tbody>
            {terms.map((row) => (
              <tr key={`${row.term}|${row.src_lang}|${row.tgt_lang}`}>
                <td>{row.term}</td>
                <td>{row.translation}</td>
                <td className="muted">{row.src_lang}→{row.tgt_lang}</td>
                <td className="muted">{row.notes || ''}</td>
                <td>
                  <button className="ghost" onClick={() => remove(row)} title="Удалить">✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
