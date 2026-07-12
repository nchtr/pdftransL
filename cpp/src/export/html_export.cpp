#include "export/html_export.h"
#include "core/models.h"
#include "parsing/splitter.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QMimeDatabase>
#include <QRegularExpression>

namespace pdftransl {

namespace {

// Невидимый разделитель (Private Use of General Punctuation) — маркер
// защищённых участков (формулы), не встречающийся в обычном тексте и не
// требующий экранирования при встраивании в regex-шаблоны.
const QChar kSentinel(0x2063);

QString sentinelToken(int idx) {
    return QString(kSentinel) + QString::number(idx) + QString(kSentinel);
}

QString escapeHtml(const QString& text) {
    QString out = text;
    out.replace(QLatin1Char('&'), QLatin1String("&amp;"));
    out.replace(QLatin1Char('<'), QLatin1String("&lt;"));
    out.replace(QLatin1Char('>'), QLatin1String("&gt;"));
    return out;
}

// Экранирует HTML, защищая математику от разметки, и применяет базовое
// инлайн-форматирование (**bold**, *italic*, `code`, ссылки, картинки).
QString inlineMarkdownToHtml(const QString& text) {
    static const QRegularExpression mathRe(
        QStringLiteral("\\$\\$[\\s\\S]+?\\$\\$|\\$(?!\\s)[^$\\n]+?(?<!\\s)\\$"));

    QStringList protectedSpans;
    QString working;
    {
        int lastEnd = 0;
        auto it = mathRe.globalMatch(text);
        while (it.hasNext()) {
            auto m = it.next();
            working += text.mid(lastEnd, m.capturedStart() - lastEnd);
            working += sentinelToken(protectedSpans.size());
            protectedSpans << m.captured(0);
            lastEnd = m.capturedEnd();
        }
        working += text.mid(lastEnd);
    }

    working = escapeHtml(working);

    static const QRegularExpression imageRe(QStringLiteral("!\\[([^\\]]*)\\]\\(([^)]+)\\)"));
    working.replace(imageRe, QStringLiteral(R"(<img alt="\1" src="\2">)"));
    static const QRegularExpression linkRe(QStringLiteral("\\[([^\\]]+)\\]\\(([^)]+)\\)"));
    working.replace(linkRe, QStringLiteral(R"(<a href="\2">\1</a>)"));
    static const QRegularExpression codeRe(QStringLiteral("`([^`]+)`"));
    working.replace(codeRe, QStringLiteral("<code>\\1</code>"));
    static const QRegularExpression boldRe(QStringLiteral("\\*\\*([^*]+)\\*\\*"));
    working.replace(boldRe, QStringLiteral("<strong>\\1</strong>"));
    static const QRegularExpression italicRe(QStringLiteral("(?<!\\*)\\*([^*\\n]+)\\*(?!\\*)"));
    working.replace(italicRe, QStringLiteral("<em>\\1</em>"));

    if (!protectedSpans.isEmpty()) {
        const QRegularExpression restoreRe(QString(kSentinel) + QStringLiteral("(\\d+)") +
                                            QString(kSentinel));
        QString out;
        int lastEnd = 0;
        auto it = restoreRe.globalMatch(working);
        while (it.hasNext()) {
            auto m = it.next();
            out += working.mid(lastEnd, m.capturedStart() - lastEnd);
            const int idx = m.captured(1).toInt();
            out += (idx >= 0 && idx < protectedSpans.size()) ? escapeHtml(protectedSpans[idx])
                                                              : QString();
            lastEnd = m.capturedEnd();
        }
        out += working.mid(lastEnd);
        working = out;
    }
    return working;
}

QString imageToDataUri(const QString& src, const QString& assetsDir) {
    if (src.startsWith(QStringLiteral("http://")) || src.startsWith(QStringLiteral("https://")) ||
        src.startsWith(QStringLiteral("data:")) || assetsDir.isEmpty()) {
        return src;
    }
    const QStringList candidates = {QDir(assetsDir).filePath(src),
                                     QDir(assetsDir).filePath(QFileInfo(src).fileName())};
    constexpr qint64 kMaxInline = 4 * 1024 * 1024;
    for (const auto& candidate : candidates) {
        QFileInfo info(candidate);
        if (info.exists() && info.isFile() && info.size() <= kMaxInline) {
            QFile file(candidate);
            if (file.open(QIODevice::ReadOnly)) {
                QMimeDatabase mimeDb;
                QString mime = mimeDb.mimeTypeForFile(info).name();
                if (mime.isEmpty()) mime = QStringLiteral("image/png");
                return QStringLiteral("data:%1;base64,%2")
                    .arg(mime, QString::fromLatin1(file.readAll().toBase64()));
            }
        }
    }
    return src;
}

QString tableToHtml(const QString& text) {
    QStringList rows;
    for (const auto& line : text.split('\n')) {
        if (line.trimmed().startsWith('|')) rows << line;
    }
    static const QRegularExpression sepCellRe(QStringLiteral("^:?-{2,}:?$"));

    QStringList out = {QStringLiteral("<table>")};
    bool headerDone = false;
    for (const auto& row : rows) {
        QString trimmed = row.trimmed();
        if (trimmed.startsWith('|')) trimmed = trimmed.mid(1);
        if (trimmed.endsWith('|')) trimmed.chop(1);
        QStringList cells;
        for (const auto& cell : trimmed.split('|')) cells << cell.trimmed();

        bool isSeparator = true;
        for (const auto& cell : cells) {
            const QString c = cell.isEmpty() ? QStringLiteral("-") : cell;
            if (!sepCellRe.match(c).hasMatch()) { isSeparator = false; break; }
        }
        if (isSeparator) { headerDone = true; continue; }

        const QString tag = (headerDone || out.size() > 1) ? QStringLiteral("td") : QStringLiteral("th");
        QString rowHtml = QStringLiteral("<tr>");
        for (const auto& cell : cells) {
            rowHtml += QStringLiteral("<%1>%2</%1>").arg(tag, inlineMarkdownToHtml(cell));
        }
        rowHtml += QStringLiteral("</tr>");
        out << rowHtml;
    }
    out << QStringLiteral("</table>");
    return out.join('\n');
}

const QString kStyle = QStringLiteral(
    "<style>\n"
    "  body { font-family: Georgia, 'Times New Roman', serif; max-width: 52rem;\n"
    "         margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #1a1a1a; }\n"
    "  h1, h2, h3, h4 { font-family: Helvetica, Arial, sans-serif; line-height: 1.25; }\n"
    "  img { max-width: 100%; height: auto; display: block; margin: 1rem auto; }\n"
    "  table { border-collapse: collapse; margin: 1rem 0; width: 100%; }\n"
    "  th, td { border: 1px solid #999; padding: 0.4rem 0.6rem; text-align: left; }\n"
    "  th { background: #f0f0f0; }\n"
    "  pre { background: #f6f6f6; padding: 0.8rem; overflow-x: auto; border-radius: 4px; }\n"
    "  code { font-family: 'SF Mono', Consolas, monospace; font-size: 0.92em; }\n"
    "  .math-display { text-align: center; margin: 1rem 0; overflow-x: auto; }\n"
    "  @media (prefers-color-scheme: dark) {\n"
    "    body { background: #1e1e1e; color: #ddd; }\n"
    "    th { background: #333; }\n"
    "    pre { background: #2a2a2a; }\n"
    "  }\n"
    "  @media print { body { max-width: none; margin: 1cm; } }\n"
    "</style>\n");

const QString kKatexHead = QStringLiteral(
    "<link rel=\"stylesheet\" "
    "href=\"https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css\">\n"
    "<script defer src=\"https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js\"></script>\n"
    "<script defer "
    "src=\"https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js\"\n"
    "        onload=\"renderMathInElement(document.body, {delimiters: [\n"
    "          {left: '$$', right: '$$', display: true},\n"
    "          {left: '$', right: '$', display: false}\n"
    "        ]});\"></script>\n");

QString markdownToHtmlBody(const QString& markdown, const QString& assetsDir) {
    QStringList parts;
    static const QRegularExpression headingRe(QStringLiteral("^(#{1,6})\\s*(.*)$"));
    static const QRegularExpression imageLineRe(
        QStringLiteral("^\\s*!\\[([^\\]]*)\\]\\(([^)]*)\\)\\s*$"));
    static const QRegularExpression fenceStart(QStringLiteral("^```[^\\n]*\\n?"));
    static const QRegularExpression fenceEnd(QStringLiteral("\\n?```$"));

    for (const auto& block : splitMarkdown(markdown)) {
        const QString& text = block.text;
        switch (block.type) {
        case BlockType::Heading: {
            auto m = headingRe.match(text);
            const int level = m.hasMatch() ? m.captured(1).size() : 1;
            const QString titleText = m.hasMatch() ? m.captured(2) : text;
            parts << QStringLiteral("<h%1>%2</h%1>").arg(level).arg(inlineMarkdownToHtml(titleText));
            break;
        }
        case BlockType::Math:
            parts << QStringLiteral("<div class=\"math-display\">%1</div>").arg(escapeHtml(text));
            break;
        case BlockType::Code: {
            QString body = text;
            body.remove(fenceStart);
            body.remove(fenceEnd);
            parts << QStringLiteral("<pre><code>%1</code></pre>").arg(escapeHtml(body));
            break;
        }
        case BlockType::Table:
            parts << tableToHtml(text);
            break;
        case BlockType::Image: {
            auto m = imageLineRe.match(text);
            const QString alt = m.hasMatch() ? m.captured(1) : QString();
            const QString src = imageToDataUri(m.hasMatch() ? m.captured(2) : QString(), assetsDir);
            QString figure =
                QStringLiteral("<figure><img alt=\"%1\" src=\"%2\">").arg(escapeHtml(alt), src);
            if (!alt.isEmpty()) figure += QStringLiteral("<figcaption>%1</figcaption>").arg(escapeHtml(alt));
            figure += QStringLiteral("</figure>");
            parts << figure;
            break;
        }
        case BlockType::Html:
            parts << text;
            break;
        default: {
            QString body = inlineMarkdownToHtml(text);
            body.replace(QLatin1Char('\n'), QStringLiteral("<br>\n"));
            parts << QStringLiteral("<p>%1</p>").arg(body);
            break;
        }
        }
    }
    return parts.join('\n');
}

} // namespace

bool exportHtml(const QString& markdown, const QString& outPath, const QString& assetsDir,
                const QString& title) {
    const QString body = markdownToHtmlBody(markdown, assetsDir);
    const QString html = QStringLiteral(
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>%1</title>\n%2%3</head>\n<body>\n%4\n</body>\n</html>\n")
        .arg(escapeHtml(title), kKatexHead, kStyle, body);

    QDir().mkpath(QFileInfo(outPath).absolutePath());
    QFile file(outPath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) return false;
    file.write(html.toUtf8());
    return true;
}

} // namespace pdftransl
