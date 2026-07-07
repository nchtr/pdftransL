import { useState } from 'react'
import { api } from '../api.js'

const LANGS = ['en', 'ru', 'de', 'fr', 'es', 'zh', 'ja', 'uk']

const PARSERS = [
  ['', 'по умолчанию (auto)'],
  ['auto', 'auto — лучший из установленных'],
  ['mineru_local', 'MinerU (локально, формулы)'],
  ['mineru_api', 'MinerU API (облако)'],
  ['vlm_ocr', 'OCR vision-моделью (сканы, DeepSeek-OCR)'],
  ['nougat', 'Nougat (формулы, GPU)'],
  ['marker', 'marker (быстрый)'],
  ['docling', 'Docling (таблицы)'],
  ['grobid', 'GROBID (структура/библиография)'],
  ['pymupdf', 'PyMuPDF (текст, мгновенно)'],
]

export default function UploadForm({ meta, onSubmitted, onError }) {
  const [file, setFile] = useState(null)
  const [sourceLang, setSourceLang] = useState('en')
  const [targetLang, setTargetLang] = useState('ru')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [parser, setParser] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [formats, setFormats] = useState(['html', 'docx', 'pdf'])
  const [options, setOptions] = useState({
    review: true,
    use_rag: true,
    bilingual: false,
    describe_figures: false,
    skip_references: true,
    quality_score: false,
  })
  const [busy, setBusy] = useState(false)

  const engines = meta?.export_engines || {}
  const providers = meta?.providers || []

  const toggleFormat = (fmt) =>
    setFormats((prev) =>
      prev.includes(fmt) ? prev.filter((f) => f !== fmt) : [...prev, fmt],
    )

  const toggleOption = (key) =>
    setOptions((prev) => ({ ...prev, [key]: !prev[key] }))

  const submit = async (e) => {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('source_lang', sourceLang)
      form.append('target_lang', targetLang)
      if (provider) form.append('provider', provider)
      if (model) form.append('model', model)
      const jobOptions = { ...options, formats }
      if (parser) jobOptions.parser_backend = parser
      if (visionModel) jobOptions.vision_model = visionModel
      form.append('options', JSON.stringify(jobOptions))
      await api.createJob(form)
      setFile(null)
      e.target.reset()
      onSubmitted()
    } catch (err) {
      onError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="upload-form">
      <label className="file-drop">
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files[0] || null)}
        />
        {file ? file.name : 'Выберите PDF-файл или перетащите сюда'}
      </label>

      <div className="row">
        <label>
          Язык оригинала
          <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
            {LANGS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
        <label>
          Язык перевода
          <select value={targetLang} onChange={(e) => setTargetLang(e.target.value)}>
            {LANGS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="row">
        <label>
          Провайдер LLM
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">по умолчанию (сервер)</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
                {p.is_local ? ' (локальный)' : p.key_configured ? '' : ' (нет ключа)'}
              </option>
            ))}
          </select>
        </label>
        <label>
          Модель перевода (необязательно)
          <input
            type="text"
            placeholder="например qwen2.5:14b или gemma3:12b"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </label>
      </div>

      <div className="row">
        <label>
          Парсер PDF
          <select value={parser} onChange={(e) => setParser(e.target.value)}>
            {PARSERS.map(([value, title]) => (
              <option key={value || 'default'} value={value}>{title}</option>
            ))}
          </select>
        </label>
        <label>
          OCR-модель (необязательно)
          <input
            type="text"
            placeholder="напр. deepseek-ai/DeepSeek-OCR"
            value={visionModel}
            onChange={(e) => setVisionModel(e.target.value)}
          />
        </label>
      </div>
      <p className="hint">
        💡 Выберите «OCR vision-моделью» для сканов — можно указать
        специализированную OCR-модель (DeepSeek-OCR) для парсинга и
        отдельную LLM для перевода. Обычная мультимодальная модель
        (gemma3, *-vl) тоже сама распознаёт сканы.
      </p>

      <fieldset>
        <legend>Форматы результата (markdown — всегда)</legend>
        {['html', 'docx', 'pdf', 'latex'].map((fmt) => (
          <label key={fmt} className="inline">
            <input
              type="checkbox"
              checked={formats.includes(fmt)}
              onChange={() => toggleFormat(fmt)}
            />
            {fmt}
            {engines[fmt] && engines[fmt].length === 0 && ' ⚠ нет движка'}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Опции</legend>
        {[
          ['review', 'LLM-ревью проблемных сегментов'],
          ['use_rag', 'Память переводов / RAG'],
          ['bilingual', 'Двуязычный документ (оригинал + перевод)'],
          ['describe_figures', 'VLM-описания рисунков'],
          ['skip_references', 'Не переводить список литературы'],
          ['quality_score', 'Оценка качества LLM-судьёй (0–100)'],
        ].map(([key, title]) => (
          <label key={key} className="inline">
            <input
              type="checkbox"
              checked={options[key]}
              onChange={() => toggleOption(key)}
            />
            {title}
          </label>
        ))}
      </fieldset>

      <button type="submit" disabled={!file || busy}>
        {busy ? 'Отправка…' : 'Перевести'}
      </button>
    </form>
  )
}
