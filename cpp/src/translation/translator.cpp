#include "translation/translator.h"
#include "quality/validators.h"
#include "translation/prompts.h"
#include <QMutexLocker>
#include <algorithm>

namespace pdftransl {

namespace {

// Хвост исходного текста, подмешиваемый как контекст соседнего сегмента —
// сглаживает швы между кусками одного абзаца/раздела.
constexpr int kContextChars = 400;

// Модели иногда заворачивают весь ответ в ```-ограду — снимаем её.
QString stripWrappingFence(const QString& raw) {
    QString text = raw.trimmed();
    if (text.startsWith("```") && text.endsWith("```") && text.size() >= 6) {
        QString body = text.mid(3, text.size() - 6);
        int firstNl = body.indexOf('\n');
        if (firstNl != -1 && !body.left(firstNl).trimmed().contains(' ')) {
            body = body.mid(firstNl + 1);
        }
        return body.trimmed();
    }
    return text;
}

QAIssue pausedIssue() {
    return QAIssue{"warning",
                    "translation paused before this segment was reached; resume the job to continue",
                    "paused"};
}

} // namespace

Translator::Translator(const PipelineConfig& config, LLMClientPtr client)
    : m_config(config), m_client(std::move(client)) {}

void Translator::setDocumentContext(const QString& docSummary, const QStringList& glossaryHints) {
    m_docSummary = docSummary;
    m_docGlossaryHints = glossaryHints;
}

void Translator::setCheckpoint(std::shared_ptr<Checkpoint> checkpoint) {
    m_checkpoint = std::move(checkpoint);
}

QString Translator::issuesToText(const std::vector<QAIssue>& issues) {
    QStringList lines;
    lines.reserve(static_cast<int>(issues.size()));
    for (const auto& issue : issues) lines << ("- " + issue.message);
    return lines.join('\n');
}

void Translator::finalizeAttempt(Segment& segment, const QString& rawTranslation,
                                  const QMap<QString, QString>& placeholders) {
    QString cleaned = stripWrappingFence(rawTranslation);
    UnmaskResult restored = unmask(cleaned, placeholders);

    segment.issues.clear();
    if (!restored.missing.isEmpty()) {
        segment.issues.push_back(QAIssue{
            "error",
            QStringLiteral("placeholders lost in translation: %1").arg(restored.missing.join(", ")),
            "placeholder_missing"});
    }
    if (!restored.unknown.isEmpty()) {
        segment.issues.push_back(QAIssue{
            "error",
            QStringLiteral("invented placeholder tokens: %1").arg(restored.unknown.join(", ")),
            "placeholder_unknown"});
    }
    segment.translation = restored.text;

    auto validation = validateSegment(segment, m_config);
    segment.issues.insert(segment.issues.end(), validation.begin(), validation.end());
    segment.ok = std::none_of(segment.issues.begin(), segment.issues.end(),
                               [](const QAIssue& issue) { return issue.level == "error"; });
}

Segment Translator::translateOne(Segment segment, const QString& sourceContext) {
    if (segment.passthrough) return segment;

    // Повтор внутри документа (колонтитул, повторная подпись, дисклеймер):
    // этот же исходный текст уже переведён в этом прогоне — берём готовое,
    // LLM не нужен.
    {
        QMutexLocker lock(&m_cacheMutex);
        auto it = m_runCache.constFind(segment.sourceText);
        if (it != m_runCache.constEnd()) {
            segment.translation = it.value();
            segment.ok = true;
            segment.issues.clear();
            segment.issues.push_back(QAIssue{
                "info", "reused translation of an identical segment from this run", "dedup"});
            return segment;
        }
    }

    MaskResult masked = m_masker.mask(segment.sourceText);
    segment.maskedText = masked.text;

    const QString system = buildTranslationSystem(m_config.sourceLang, m_config.targetLang,
                                                    m_docSummary, m_docGlossaryHints);
    const QString user = buildUserMessage(masked.text, sourceContext);

    QString raw;
    try {
        QMutexLocker lock(&m_clientMutex);
        raw = m_client->chat({Message{"system", system}, Message{"user", user}}, m_config.temperature);
    } catch (const std::exception& exc) {
        segment.ok = false;
        segment.issues.push_back(QAIssue{
            "error", QStringLiteral("translation call failed: %1").arg(exc.what()), "exception"});
        return segment;
    }
    finalizeAttempt(segment, raw, masked.placeholders);

    // Цикл самопочинки: список проблем от валидаторов уходит модели вместе
    // с её же прошлым ответом — пока не ок или не кончились попытки. Сетевые
    // ретраи/бэкоффы живут в LLM-клиенте; здесь только осмысленные повторные
    // запросы по существу проблемы.
    int attempts = 1;
    while (!segment.ok && attempts <= m_config.maxRepairAttempts) {
        const QString repairUser = REPAIR_USER.arg(issuesToText(segment.issues), masked.text, raw);
        QString newRaw;
        try {
            QMutexLocker lock(&m_clientMutex);
            newRaw = m_client->chat({Message{"system", system}, Message{"user", repairUser}},
                                     m_config.temperature);
        } catch (const std::exception& exc) {
            segment.issues.push_back(QAIssue{
                "error", QStringLiteral("repair call failed: %1").arg(exc.what()), "exception"});
            break;
        }
        ++attempts;
        // Модель «упёрлась»: дословно повторила прошлый ответ — новые
        // попытки дадут то же самое, не жжём вызовы впустую.
        const bool stuck = newRaw.trimmed() == raw.trimmed();
        raw = newRaw;
        finalizeAttempt(segment, raw, masked.placeholders);
        if (stuck && !segment.ok) break;
    }

    if (segment.ok && !segment.translation.isEmpty()) {
        QMutexLocker lock(&m_cacheMutex);
        m_runCache.insert(segment.sourceText, segment.translation);
    }
    return segment;
}

std::vector<Segment> Translator::translateSegments(
    const std::vector<Segment>& segments, ShouldPauseFn shouldPause, ProgressFn onProgress) {

    std::vector<Segment> result = segments;

    QMap<int, Segment> resumed;
    if (m_checkpoint) resumed = m_checkpoint->load();

    { // Свежий кэш повторов на этот документ: не тащим переводы из
      // предыдущих вызовов этого же экземпляра.
        QMutexLocker lock(&m_cacheMutex);
        m_runCache.clear();
    }

    // Контекст с исходной стороны: хвост предыдущего непустого сегмента,
    // вычисленный по исходному порядку документа (безопасно для параллели).
    QHash<int, QString> contextByIndex;
    QString prevSource;
    for (size_t i = 0; i < result.size(); ++i) {
        if (!result[i].passthrough && !prevSource.isEmpty()) {
            contextByIndex.insert(static_cast<int>(i), prevSource.right(kContextChars));
        }
        if (!result[i].sourceText.trimmed().isEmpty()) prevSource = result[i].sourceText;
    }

    std::vector<int> toTranslate;
    toTranslate.reserve(result.size());
    for (size_t i = 0; i < result.size(); ++i) {
        if (result[i].passthrough) continue;
        auto found = resumed.find(static_cast<int>(i));
        if (found != resumed.end() && found->ok) {
            result[i] = found.value();
            continue;
        }
        toTranslate.push_back(static_cast<int>(i));
    }

    const int total = static_cast<int>(toTranslate.size());
    int done = 0;
    if (onProgress) onProgress(done, total);
    if (total == 0) return result;

    const int batchSize = m_config.translateBatchSize > 0 ? m_config.translateBatchSize : total;
    bool paused = false;
    for (size_t start = 0; start < toTranslate.size(); start += static_cast<size_t>(batchSize)) {
        // Пауза, пришедшая между партиями, замечается ДО отправки новых
        // запросов — партия даже не стартует.
        if (!paused && shouldPause && shouldPause()) paused = true;
        if (paused) {
            for (size_t k = start; k < toTranslate.size(); ++k) {
                result[toTranslate[k]].issues.push_back(pausedIssue());
            }
            break;
        }

        const size_t end = std::min(start + static_cast<size_t>(batchSize), toTranslate.size());
        // Concrete Qt network clients own a QNetworkAccessManager, which is
        // thread-affine.  Calling that one shared client from a worker pool is
        // invalid even if a mutex serializes calls, because the caller is
        // still the wrong thread.  Keep transport on the Translator's owning
        // thread until clients are redesigned as one-per-worker objects.
        for (size_t k = start; k < end; ++k) {
            const int idx = toTranslate[k];
            const QString ctx = contextByIndex.value(idx);
            result[idx] = translateOne(std::move(result[idx]), ctx);
        }

        for (size_t k = start; k < end; ++k) {
            const int idx = toTranslate[k];
            if (m_checkpoint && result[idx].ok) m_checkpoint->save(idx, result[idx]);
        }

        done += static_cast<int>(end - start);
        if (onProgress) onProgress(done, total);
    }
    return result;
}

} // namespace pdftransl
