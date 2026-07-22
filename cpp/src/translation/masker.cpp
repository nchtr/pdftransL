#include "translation/masker.h"
#include <QMutexLocker>
#include <QRegularExpression>
#include <QSet>
#include <QVector>

namespace pdftransl {

namespace {

// Порядок важен: крупные конструкции маскируются раньше своих частей
// (код-блок раньше инлайн-кода, $$...$$ раньше $...$ и т.д.).
const QVector<QRegularExpression>& maskPatterns() {
    static const QVector<QRegularExpression> patterns = {
        QRegularExpression(QStringLiteral(R"(```[\s\S]*?```)")),
        QRegularExpression(QStringLiteral(R"(\$\$[\s\S]*?\$\$)")),
        QRegularExpression(QStringLiteral(R"(\\begin\{([a-zA-Z*]+)\}[\s\S]*?\\end\{\1\})")),
        QRegularExpression(QStringLiteral(R"(\\\[[\s\S]*?\\\])")),
        QRegularExpression(QStringLiteral(R"(\\\([\s\S]*?\\\))")),
        // $...$ inline math: no space right after opening / before closing $,
        // single line — avoids catching "$5 and $6" style currency text.
        QRegularExpression(QStringLiteral(R"(\$(?!\s)[^$\n]+?(?<![\s\\])\$)")),
        QRegularExpression(QStringLiteral(R"(`[^`\n]+`)")),
        QRegularExpression(QStringLiteral(R"(!\[[^\]]*\]\([^)]*\))")),
        // Mask "](url)" of markdown links, keeping the link text translatable.
        QRegularExpression(QStringLiteral(R"(\]\([^)\s]+\))")),
        QRegularExpression(QStringLiteral(
            R"(\\(?:cite[tp]?|ref|eqref|autoref|cref|label|url|href)\*?\{[^}]*\})")),
        QRegularExpression(QStringLiteral(R"(\[\d+(?:\s*[,;\x{2013}-]\s*\d+)*\])")),
        // ⟦⟧ excluded: a URL/tag must not swallow an already-inserted
        // placeholder token, or the mapping nests.
        QRegularExpression(QStringLiteral(R"(https?://[^\s)\]>\x{27E6}\x{27E7}]+)")),
        QRegularExpression(QStringLiteral(R"(</?[a-zA-Z][^<>\n\x{27E6}\x{27E7}]*>)")),
    };
    return patterns;
}

QString placeholderToken(qint64 index) {
    return QStringLiteral("⟦PH%1⟧").arg(index);
}

const QRegularExpression& placeholderRe() {
    static const QRegularExpression re(QStringLiteral("⟦PH(\\d+)⟧"));
    return re;
}

// Толерантный матчер плейсхолдера, который модель могла покорёжить: другие
// скобки ([ 【 〚 «), лишние пробелы, регистр, кириллические гомоглифы Р/Н
// вместо P/H, удвоенные скобки. Скобки обязательны с ОБЕИХ сторон — иначе
// «pH 12» из химии дало бы ложное срабатывание.
const QRegularExpression& fuzzyPlaceholderRe() {
    static const QRegularExpression re(QStringLiteral(
        "[⟦\\[【〚〔｢«‹<]{1,2}\\s*"
        "[PpРр]\\s*[HhНн]\\s*(\\d+)\\s*"
        "[⟧\\]】〛〕｣»›>]{1,2}"));
    return re;
}

QString applyMaskPattern(const QString& input, const QRegularExpression& re,
                          QMap<QString, QString>& mapping, qint64& counter) {
    QString out;
    out.reserve(input.size());
    int lastEnd = 0;
    QRegularExpressionMatchIterator it = re.globalMatch(input);
    while (it.hasNext()) {
        QRegularExpressionMatch m = it.next();
        if (m.capturedStart() < lastEnd) continue; // overlap guard
        out += input.mid(lastEnd, m.capturedStart() - lastEnd);
        QString token = placeholderToken(counter++);
        mapping.insert(token, m.captured(0));
        out += token;
        lastEnd = m.capturedEnd();
    }
    out += input.mid(lastEnd);
    return out;
}

bool applyExactPass(QString& text, const QMap<QString, QString>& mapping, QSet<QString>& seen) {
    bool any = false;
    for (auto it = mapping.constBegin(); it != mapping.constEnd(); ++it) {
        if (!seen.contains(it.key()) && text.contains(it.key())) {
            text.replace(it.key(), it.value());
            seen.insert(it.key());
            any = true;
        }
    }
    return any;
}

bool applyFuzzyPass(QString& text, const QMap<QString, QString>& mapping, QSet<QString>& seen) {
    bool changed = false;
    QString out;
    out.reserve(text.size());
    int lastEnd = 0;
    QRegularExpressionMatchIterator it = fuzzyPlaceholderRe().globalMatch(text);
    while (it.hasNext()) {
        QRegularExpressionMatch m = it.next();
        out += text.mid(lastEnd, m.capturedStart() - lastEnd);
        QString token = placeholderToken(m.captured(1).toLongLong());
        auto found = mapping.constFind(token);
        if (found != mapping.constEnd()) {
            out += found.value();
            seen.insert(token);
            changed = true;
        } else {
            out += m.captured(0); // hallucinated / stray — leave as-is
        }
        lastEnd = m.capturedEnd();
    }
    out += text.mid(lastEnd);
    if (changed) text = out;
    return changed;
}

} // namespace

Masker::Masker(qint64 start) : m_counter(start) {}

MaskResult Masker::mask(const QString& text) {
    QMutexLocker lock(&m_mutex);
    MaskResult result;
    QString masked = text;
    for (const auto& pattern : maskPatterns()) {
        masked = applyMaskPattern(masked, pattern, result.placeholders, m_counter);
    }
    result.text = masked;
    return result;
}

UnmaskResult unmask(const QString& text, const QMap<QString, QString>& placeholders) {
    UnmaskResult result;
    QString restored = text;
    QSet<QString> seen;

    // Each pass can reveal at most one more nesting level; +2 for safety.
    const int maxPasses = static_cast<int>(placeholders.size()) + 2;
    for (int i = 0; i < maxPasses; ++i) {
        bool exactChanged = applyExactPass(restored, placeholders, seen);
        bool fuzzyChanged = applyFuzzyPass(restored, placeholders, seen);
        if (!exactChanged && !fuzzyChanged) break;
    }

    for (auto it = placeholders.constBegin(); it != placeholders.constEnd(); ++it) {
        if (!seen.contains(it.key())) result.missing << it.key();
    }

    QRegularExpressionMatchIterator it = fuzzyPlaceholderRe().globalMatch(restored);
    while (it.hasNext()) {
        QRegularExpressionMatch m = it.next();
        QString token = placeholderToken(m.captured(1).toLongLong());
        if (!placeholders.contains(token)) result.unknown << m.captured(0);
    }

    result.text = restored;
    return result;
}

QString stripPlaceholders(const QString& text) {
    QString out = text;
    out.replace(placeholderRe(), QStringLiteral(" "));
    return out;
}

} // namespace pdftransl
