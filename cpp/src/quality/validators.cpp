#include "quality/validators.h"
#include "translation/masker.h"
#include <QMap>
#include <QRegularExpression>
#include <algorithm>
#include <cmath>

namespace pdftransl {

namespace {

enum class Script { None, Latin, Cyrillic, Cjk };

Script scriptOf(const QString& lang) {
    static const QMap<QString, Script> table = {
        {"en", Script::Latin}, {"de", Script::Latin}, {"fr", Script::Latin}, {"es", Script::Latin},
        {"ru", Script::Cyrillic}, {"uk", Script::Cyrillic}, {"kk", Script::Cyrillic},
        {"zh", Script::Cjk}, {"ja", Script::Cjk},
    };
    return table.value(lang.toLower(), Script::None);
}

const QRegularExpression& scriptPattern(Script script) {
    static const QRegularExpression latin(QStringLiteral("[A-Za-z]"));
    static const QRegularExpression cyrillic(QStringLiteral("[А-Яа-яЁё]"));
    static const QRegularExpression cjk(QStringLiteral("[\\x{4E00}-\\x{9FFF}\\x{3040}-\\x{30FF}]"));
    switch (script) {
        case Script::Latin: return latin;
        case Script::Cyrillic: return cyrillic;
        case Script::Cjk: return cjk;
        default: return latin;
    }
}

// «Плотность» письменности: иероглифика несёт в ~2.5 раза больше информации
// на символ, чем алфавитные языки — без поправки нормальный CJK-перевод
// абзаца выглядел бы "подозрительно коротким".
constexpr double kCjkDensity = 2.5;

double lengthScale(const QString& sourceLang, const QString& targetLang) {
    Script src = scriptOf(sourceLang);
    Script tgt = scriptOf(targetLang);
    if (tgt == Script::Cjk && src != Script::Cjk) return kCjkDensity;
    if (src == Script::Cjk && tgt != Script::Cjk) return 1.0 / kCjkDensity;
    return 1.0;
}

const QRegularExpression& wordRe() {
    static const QRegularExpression re(QStringLiteral("[^\\W\\d_]{2,}"),
                                        QRegularExpression::UseUnicodePropertiesOption);
    return re;
}

double scriptWordRatio(const QString& text, Script script) {
    int total = 0;
    int hits = 0;
    const QRegularExpression& pattern = scriptPattern(script);
    QRegularExpressionMatchIterator it = wordRe().globalMatch(text);
    while (it.hasNext()) {
        QString word = it.next().captured(0);
        ++total;
        if (pattern.match(word).hasMatch()) ++hits;
    }
    return total == 0 ? 0.0 : static_cast<double>(hits) / total;
}

int countMatches(const QRegularExpression& re, const QString& text) {
    int n = 0;
    auto it = re.globalMatch(text);
    while (it.hasNext()) { it.next(); ++n; }
    return n;
}

const QRegularExpression& headingRe() {
    static const QRegularExpression re(QStringLiteral("(?m)^#{1,6}\\s"));
    return re;
}

QStringList tableRows(const QString& text) {
    QStringList rows;
    for (const auto& line : text.split('\n')) {
        if (line.trimmed().startsWith('|')) rows << line;
    }
    return rows;
}

} // namespace

double residualSourceRatio(const QString& text, const QString& sourceLang,
                            const QString& targetLang) {
    Script src = scriptOf(sourceLang);
    Script tgt = scriptOf(targetLang);
    if (src == Script::None || tgt == Script::None || src == tgt) return 0.0;
    return scriptWordRatio(stripPlaceholders(text), src);
}

std::vector<QAIssue> validateSegment(const Segment& segment, const PipelineConfig& config) {
    std::vector<QAIssue> issues;
    if (segment.passthrough) return issues; // непереводимые блоки не проверяем

    const QString& source = segment.sourceText;
    const QString translation = segment.translation;

    if (translation.trimmed().isEmpty()) {
        issues.push_back(QAIssue{"error", "translation is empty", "empty_translation"});
        return issues;
    }

    // 1. Длина: отношение перевод/оригинал, нормализованное по плотности
    // письменности (см. lengthScale).
    const QString maskedOrSource = segment.maskedText.isEmpty() ? source : segment.maskedText;
    const int srcLen = std::max(1, static_cast<int>(stripPlaceholders(maskedOrSource).size()));
    const double ratio = (static_cast<double>(translation.size()) / srcLen) *
                          lengthScale(config.sourceLang, config.targetLang);
    if (ratio < config.minLengthRatio) {
        issues.push_back(QAIssue{
            "error",
            QStringLiteral("translation suspiciously short (ratio %1); possible omitted content")
                .arg(ratio, 0, 'f', 2),
            "too_short"});
    } else if (ratio > config.maxLengthRatio) {
        issues.push_back(QAIssue{
            "error",
            QStringLiteral("translation suspiciously long (ratio %1); possible added content "
                            "or repetition loop")
                .arg(ratio, 0, 'f', 2),
            "too_long"});
    }

    // 2. Остаток исходного языка (недопереведённые куски).
    if (source.size() > 200) {
        const double residual = residualSourceRatio(translation, config.sourceLang, config.targetLang);
        if (residual > config.maxResidualSourceRatio) {
            issues.push_back(QAIssue{
                "error",
                QStringLiteral("%1% of words still in source language; text appears "
                                "(partially) untranslated")
                    .arg(static_cast<int>(residual * 100)),
                "untranslated"});
        }
    }

    // 3. Остаточные плейсхолдер-подобные токены (не должно остаться ни
    // одного после unmask() — независимая подстраховка от ошибок в
    // Translator).
    static const QRegularExpression leftoverPh(QStringLiteral("⟦PH\\d+⟧"));
    if (leftoverPh.match(translation).hasMatch()) {
        issues.push_back(QAIssue{
            "error", "unresolved placeholder token left in the translation", "placeholder_residual"});
    }

    // 4. Markdown-структура: число заголовков.
    const int srcHeadings = countMatches(headingRe(), source);
    const int tgtHeadings = countMatches(headingRe(), translation);
    if (srcHeadings != tgtHeadings) {
        issues.push_back(QAIssue{
            "warning",
            QStringLiteral("heading count changed: %1 -> %2").arg(srcHeadings).arg(tgtHeadings),
            "heading_mismatch"});
    }

    // 5. Форма таблицы: число строк и колонок.
    const QStringList srcRows = tableRows(source);
    const QStringList tgtRows = tableRows(translation);
    if (!srcRows.isEmpty()) {
        if (srcRows.size() != tgtRows.size()) {
            issues.push_back(QAIssue{
                "error",
                QStringLiteral("table row count changed: %1 -> %2")
                    .arg(srcRows.size())
                    .arg(tgtRows.size()),
                "table_rows"});
        } else {
            auto maxPipes = [](const QStringList& rows) {
                int m = 0;
                for (const auto& r : rows) m = std::max(m, static_cast<int>(r.count('|')));
                return m;
            };
            const int srcCols = maxPipes(srcRows);
            const int tgtCols = tgtRows.isEmpty() ? 0 : maxPipes(tgtRows);
            if (srcCols != tgtCols) {
                issues.push_back(QAIssue{
                    "warning",
                    QStringLiteral("table column count changed: %1 -> %2").arg(srcCols).arg(tgtCols),
                    "table_cols"});
            }
        }
    }

    // 6. Несбалансированные разделители формул/окружений, добавленные моделью.
    if (translation.count("$$") % 2 != 0) {
        issues.push_back(QAIssue{
            "warning", "odd number of $$ delimiters in translation", "math_delimiters"});
    }
    static const QRegularExpression beginRe(QStringLiteral("\\\\begin\\{"));
    static const QRegularExpression endRe(QStringLiteral("\\\\end\\{"));
    const int begins = countMatches(beginRe, translation);
    const int ends = countMatches(endRe, translation);
    if (begins != ends) {
        issues.push_back(QAIssue{
            "warning",
            QStringLiteral("\\begin/\\end mismatch in translation (%1/%2)").arg(begins).arg(ends),
            "latex_env"});
    }

    return issues;
}

} // namespace pdftransl
