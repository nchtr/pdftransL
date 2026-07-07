import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Серверные настройки «налету»: сохраняются в БД и применяются ко всем
// новым задачам сразу, без перезапуска. Пустое поле = использовать
// значение по умолчанию (env / встроенное).

const BOOL_OPTIONS = [
  ['review', 'LLM-ревью проблемных сегментов'],
  ['use_rag', 'Память переводов / RAG'],
  ['learn', 'Пополнять память переводов'],
  ['doc_summary', 'Саммари документа в промпте'],
  ['auto_glossary', 'Авто-глоссарий документа'],
  ['skip_references', 'Не переводить список литературы'],
  ['ocr_on_scan', 'Авто-OCR для сканов/битых PDF'],
  ['parser_fallback', 'Фолбэк парсеров при сбое'],
  ['adaptive_throttle', 'Пауза всех потоков при 429'],
  ['fix_latex', 'LLM-починка битых формул'],
  ['quality_score', 'Оценка качества LLM-судьёй'],
  ['bilingual', 'Двуязычный документ'],
  ['describe_figures', 'VLM-описания рисунков'],
  ['parse_cache', 'Кэш парсинга'],
]

export default function SettingsPanel({ meta, onError }) {
  const [stored, setStored] = useState(null)   // сохранённые оверрайды
  const [defaults, setDefaults] = useState({})
  const [draft, setDraft] = useState({})
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState(null)

  useEffect(() => {
    api
      .settings()
      .then((data) => {
        setStored(data.settings)
        setDefaults(data.defaults || {})
        setDraft(data.settings)
      })
      .catch((e) => onError(e.message))
  }, [onError])

  if (stored === null) return <p className="muted">Загрузка…</p>

  const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setSaving(true)
    try {
      // null для очищенных полей — сервер удалит оверрайд
      const payload = {}
      const keys = new Set([...Object.keys(draft), ...Object.keys(stored)])
      keys.forEach((key) => {
        payload[key] = draft[key] === '' || draft[key] === undefined ? null : draft[key]
      })
      const result = await api.saveSettings(payload)
      setStored(result.settings)
      setDraft(result.settings)
      setSavedAt(new Date())
    } catch (e) {
      onError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const providers = meta?.providers || []
  const text = (key, placeholder, title) => (
    <label key={key}>
      {title}
      <input
        type="text"
        placeholder={placeholder}
        value={draft[key] ?? ''}
        onChange={(e) => set(key, e.target.value)}
      />
    </label>
  )

  return (
    <div className="settings-panel">
      <p className="hint">
        ⚙️ Эти значения становятся серверными умолчаниями для всех новых
        задач сразу после сохранения — перезапуск не нужен. Пустое поле —
        вернуться к умолчанию. Параметры, выбранные в форме перевода,
        по-прежнему приоритетнее.
      </p>

      <div className="settings-grid">
        <label>
          Провайдер по умолчанию
          <select value={draft.provider ?? ''} onChange={(e) => set('provider', e.target.value)}>
            <option value="">— из env ({defaults.provider})</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </label>
        {text('model', defaults.model || 'например gemma3:12b', 'Модель')}
        {text('vision_model', defaults.vision_model || 'например qwen2.5-vl', 'Vision-модель (OCR/рисунки)')}
        <label>
          Парсер
          <select value={draft.parser_backend ?? ''} onChange={(e) => set('parser_backend', e.target.value)}>
            <option value="">— auto</option>
            {['mineru_local', 'mineru_api', 'marker', 'docling', 'vlm_ocr', 'pymupdf'].map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </label>
        {text('max_workers', String(defaults.max_workers ?? 4), 'Параллельных переводов')}
        {text('rpm_limit', 'без лимита', 'Лимит запросов/мин')}
        {text('parser_timeout', String(defaults.parser_timeout ?? 1800), 'Таймаут парсера, сек')}
        {text('formats', (defaults.formats || []).join(','), 'Форматы (через запятую)')}
        {text('fallback_providers', 'например openrouter', 'Fallback-провайдеры')}
        <label>
          Уровень логов (сразу)
          <select value={draft.log_level ?? ''} onChange={(e) => set('log_level', e.target.value)}>
            <option value="">— {defaults.log_level || 'INFO'}</option>
            {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>

      <fieldset>
        <legend>Поведение пайплайна (пусто = умолчание)</legend>
        {BOOL_OPTIONS.map(([key, title]) => (
          <label key={key} className="inline tri-state">
            <select
              value={draft[key] === undefined || draft[key] === '' ? '' : String(draft[key])}
              onChange={(e) =>
                set(key, e.target.value === '' ? '' : e.target.value === 'true')
              }
            >
              <option value="">—</option>
              <option value="true">вкл</option>
              <option value="false">выкл</option>
            </select>
            {title}
          </label>
        ))}
      </fieldset>

      <div className="actions">
        <button onClick={save} disabled={saving}>
          {saving ? 'Сохранение…' : 'Сохранить настройки'}
        </button>
        {savedAt && (
          <span className="muted">
            Сохранено {savedAt.toLocaleTimeString()} — действует для новых задач
          </span>
        )}
      </div>
    </div>
  )
}
