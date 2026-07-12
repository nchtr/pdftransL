#include "quality/reviewer.h"
#include "core/config.h"
#include "quality/validators.h"
#include "translation/prompts.h"
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QRegularExpression>
#include <QSet>
#include <algorithm>
#include <optional>

namespace pdftransl {

namespace {

QSet<QString> placeholderTokensIn(const QString& text) {
    QSet<QString> tokens;
    static const QRegularExpression re(QStringLiteral("⟦PH\\d+⟧"));
    auto it = re.globalMatch(text);
    while (it.hasNext()) tokens.insert(it.next().captured(0));
    return tokens;
}

QString stripFence(const QString& raw) {
    QString text = raw.trimmed();
    if (text.startsWith("```")) {
        static const QRegularExpression fenceStart(QStringLiteral("^```[a-zA-Z]*\\n?"));
        static const QRegularExpression fenceEnd(QStringLiteral("\\n?```$"));
        text.remove(fenceStart);
        text.remove(fenceEnd);
    }
    return text;
}

// Модель могла обернуть JSON пояснением или markdown-оградой — сначала
// пробуем распарсить весь ответ, затем вытаскиваем первый похожий на объект
// фрагмент.
std::optional<QJsonObject> parseVerdict(const QString& raw) {
    const QString text = stripFence(raw);
    QJsonParseError err{};
    QJsonDocument doc = QJsonDocument::fromJson(text.toUtf8(), &err);
    if (err.error == QJsonParseError::NoError && doc.isObject()) return doc.object();

    static const QRegularExpression jsonRe(QStringLiteral("\\{[\\s\\S]*\\}"));
    auto match = jsonRe.match(text);
    if (match.hasMatch()) {
        QJsonDocument doc2 = QJsonDocument::fromJson(match.captured(0).toUtf8(), &err);
        if (err.error == QJsonParseError::NoError && doc2.isObject()) return doc2.object();
    }
    return std::nullopt;
}

} // namespace

Reviewer::Reviewer(LLMClientPtr client) : m_client(std::move(client)) {}

Segment Reviewer::reviewOne(Segment segment, const QString& sourceLang, const QString& targetLang) {
    if (segment.passthrough || segment.translation.trimmed().isEmpty()) return segment;

    const QString system = REVIEW_SYSTEM.arg(langName(sourceLang), langName(targetLang));
    const QString sourceForReview =
        segment.maskedText.isEmpty() ? segment.sourceText : segment.maskedText;
    const QString user = REVIEW_USER.arg(sourceForReview, segment.translation);

    QString raw;
    try {
        raw = m_client->chat({Message{"system", system}, Message{"user", user}}, 0.0);
    } catch (const std::exception& exc) {
        segment.issues.push_back(QAIssue{
            "warning", QStringLiteral("review pass failed: %1").arg(exc.what()), "review_error"});
        return segment;
    }

    auto verdict = parseVerdict(raw);
    if (!verdict) {
        segment.issues.push_back(
            QAIssue{"warning", "reviewer returned unparsable output", "review_unparsed"});
        return segment;
    }

    if (verdict->value("ok").toBool(false)) {
        segment.issues.push_back(QAIssue{"info", "approved by LLM reviewer", "reviewed_ok"});
        return segment;
    }

    const QJsonValue revisedVal = verdict->value("revised");
    if (revisedVal.isString() && !revisedVal.toString().trimmed().isEmpty()) {
        const QString revised = revisedVal.toString().trimmed();

        // Ревизия принимается только если не теряет ни одного плейсхолдера,
        // присутствовавшего в замаскированном источнике сегмента (формулы,
        // ссылки, код).
        const QSet<QString> required = placeholderTokensIn(sourceForReview);
        const QSet<QString> present = placeholderTokensIn(revised);
        bool lost = false;
        for (const auto& token : required) {
            if (!present.contains(token)) { lost = true; break; }
        }

        if (!lost) {
            segment.translation = revised;
            PipelineConfig cfg; // только языковая пара важна для валидатора
            cfg.sourceLang = sourceLang;
            cfg.targetLang = targetLang;
            segment.issues = validateSegment(segment, cfg);
            segment.ok = std::none_of(segment.issues.begin(), segment.issues.end(),
                                       [](const QAIssue& i) { return i.level == "error"; });
            QString notes = verdict->value("notes").toString();
            segment.issues.push_back(QAIssue{
                "info",
                QStringLiteral("revised by LLM reviewer: %1").arg(notes.left(300)),
                "reviewed_revised"});
        } else {
            segment.issues.push_back(QAIssue{
                "warning", "reviewer revision dropped placeholders; kept original",
                "review_rejected"});
        }
    }
    return segment;
}

std::vector<Segment> Reviewer::reviewSegments(const std::vector<Segment>& flagged,
                                               const QString& sourceLang,
                                               const QString& targetLang) {
    std::vector<Segment> result;
    result.reserve(flagged.size());
    for (const auto& segment : flagged) {
        // Изоляция: сбой на одном сегменте (кривой JSON модели и т.п.) не
        // должен обрывать ревью остальных.
        try {
            result.push_back(reviewOne(segment, sourceLang, targetLang));
        } catch (const std::exception& exc) {
            Segment copy = segment;
            copy.issues.push_back(QAIssue{
                "warning", QStringLiteral("review pass crashed: %1").arg(exc.what()), "review_error"});
            result.push_back(copy);
        }
    }
    return result;
}

} // namespace pdftransl
