#include "export/latex_export.h"
#include "core/models.h"
#include "parsing/splitter.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QMap>
#include <QRegularExpression>
#include <QVector>
#include <algorithm>

namespace pdftransl {

namespace {

const QChar kSentinel(0x2063); // невидимый разделитель — маркер защищённых участков

QString sentinelToken(int idx) {
    return QString(kSentinel) + QString::number(idx) + QString(kSentinel);
}

// Символы, обязательные к экранированию в текстовом режиме LaTeX. Один
// проход посимвольно — без риска повторного экранирования уже вставленных
// управляющих последовательностей (как было бы при последовательных
// str.replace).
QString escapeLatexText(const QString& text) {
    QString out;
    out.reserve(text.size());
    for (const QChar ch : text) {
        switch (ch.unicode()) {
        case '\\': out += QStringLiteral("\\textbackslash{}"); break;
        case '&': out += QStringLiteral("\\&"); break;
        case '%': out += QStringLiteral("\\%"); break;
        case '#': out += QStringLiteral("\\#"); break;
        case '_': out += QStringLiteral("\\_"); break;
        case '{': out += QStringLiteral("\\{"); break;
        case '}': out += QStringLiteral("\\}"); break;
        case '~': out += QStringLiteral("\\textasciitilde{}"); break;
        case '^': out += QStringLiteral("\\textasciicircum{}"); break;
        default: out += ch;
        }
    }
    return out;
}

// Экранирует текст, защищая математику и уже существующие LaTeX-команды
// (\cite{...}, \ref{...}, ...) от экранирования, и применяет базовое
// инлайн-форматирование.
QString inlineToLatex(const QString& text) {
    static const QRegularExpression protectRe(QStringLiteral(
        "\\$\\$[\\s\\S]+?\\$\\$|\\$(?!\\s)[^$\\n]+?(?<!\\s)\\$|\\\\[a-zA-Z]+(?:\\{[^}]*\\})*"));

    QStringList protectedSpans;
    QString working;
    {
        int lastEnd = 0;
        auto it = protectRe.globalMatch(text);
        while (it.hasNext()) {
            auto m = it.next();
            working += text.mid(lastEnd, m.capturedStart() - lastEnd);
            working += sentinelToken(protectedSpans.size());
            protectedSpans << m.captured(0);
            lastEnd = m.capturedEnd();
        }
        working += text.mid(lastEnd);
    }

    working = escapeLatexText(working);

    static const QRegularExpression boldRe(QStringLiteral("\\*\\*([^*]+)\\*\\*"));
    working.replace(boldRe, QStringLiteral("\\textbf{\\1}"));
    static const QRegularExpression italicRe(QStringLiteral("(?<!\\*)\\*([^*\\n]+)\\*(?!\\*)"));
    working.replace(italicRe, QStringLiteral("\\emph{\\1}"));
    static const QRegularExpression codeRe(QStringLiteral("`([^`]+)`"));
    working.replace(codeRe, QStringLiteral("\\texttt{\\1}"));
    // images at inline level: keep only the alt text (block-level images use \includegraphics)
    static const QRegularExpression imageRe(QStringLiteral("!\\[([^\\]]*)\\]\\(([^)]+)\\)"));
    working.replace(imageRe, QStringLiteral("\\1"));
    static const QRegularExpression linkRe(QStringLiteral("\\[([^\\]]+)\\]\\(([^)]+)\\)"));
    working.replace(linkRe, QStringLiteral("\\href{\\2}{\\1}"));

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
            out += (idx >= 0 && idx < protectedSpans.size()) ? protectedSpans[idx] : QString();
            lastEnd = m.capturedEnd();
        }
        out += working.mid(lastEnd);
        working = out;
    }
    return working;
}

QString tableToLatex(const QString& text) {
    QStringList rows;
    for (const auto& line : text.split('\n')) {
        if (line.trimmed().startsWith('|')) rows << line;
    }
    static const QRegularExpression sepCellRe(QStringLiteral("^:?-{2,}:?$"));

    QVector<QStringList> grid;
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
        if (isSeparator) continue;
        grid << cells;
    }
    if (grid.isEmpty()) return {};

    int cols = 0;
    for (const auto& row : grid) cols = std::max(cols, static_cast<int>(row.size()));

    QStringList lines = {QStringLiteral("\\begin{table}[h]"), QStringLiteral("\\centering"),
                         QStringLiteral("\\begin{tabular}{%1}").arg(QString("l").repeated(cols)),
                         QStringLiteral("\\toprule")};
    for (int i = 0; i < grid.size(); ++i) {
        QStringList padded = grid[i];
        while (padded.size() < cols) padded << QString();
        QStringList cellsLatex;
        for (const auto& cell : padded) cellsLatex << inlineToLatex(cell);
        lines << (cellsLatex.join(QStringLiteral(" & ")) + QStringLiteral(" \\\\"));
        if (i == 0) lines << QStringLiteral("\\midrule");
    }
    lines << QStringLiteral("\\bottomrule") << QStringLiteral("\\end{tabular}")
          << QStringLiteral("\\end{table}");
    return lines.join('\n');
}

const QString kPreamble = QStringLiteral(
    "\\documentclass[11pt]{article}\n"
    "\\usepackage[utf8]{inputenc}\n"
    "\\usepackage{fontspec}\n"
    "\\usepackage{amsmath, amssymb}\n"
    "\\usepackage{graphicx}\n"
    "\\usepackage{booktabs}\n"
    "\\usepackage[margin=2.5cm]{geometry}\n"
    "\\usepackage{hyperref}\n"
    "\\setmainfont{DejaVu Serif}\n"
    "\\begin{document}\n");

const QString kClosing = QStringLiteral("\n\\end{document}\n");

const QMap<int, QString>& sectionByLevel() {
    static const QMap<int, QString> map = {
        {1, "section"}, {2, "subsection"}, {3, "subsubsection"},
        {4, "paragraph"}, {5, "subparagraph"}, {6, "subparagraph"},
    };
    return map;
}

} // namespace

bool exportLatex(const QString& markdown, const QString& outPath, const QString& title) {
    QStringList parts = {kPreamble};
    if (!title.isEmpty()) {
        parts << QStringLiteral("\\title{%1}\n\\date{}\n\\maketitle\n").arg(escapeLatexText(title));
    }

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
            parts << QStringLiteral("\\%1{%2}")
                         .arg(sectionByLevel().value(level, "paragraph"), inlineToLatex(titleText));
            break;
        }
        case BlockType::Math: {
            QString body = text.trimmed();
            if (body.startsWith(QStringLiteral("$$")) && body.endsWith(QStringLiteral("$$")) &&
                body.size() >= 4) {
                body = QStringLiteral("\\[\n") + body.mid(2, body.size() - 4).trimmed() +
                       QStringLiteral("\n\\]");
            }
            parts << body;
            break;
        }
        case BlockType::Code: {
            QString body = text;
            body.remove(fenceStart);
            body.remove(fenceEnd);
            parts << (QStringLiteral("\\begin{verbatim}\n") + body + QStringLiteral("\n\\end{verbatim}"));
            break;
        }
        case BlockType::Table:
            parts << tableToLatex(text);
            break;
        case BlockType::Image: {
            auto m = imageLineRe.match(text);
            const QString alt = m.hasMatch() ? m.captured(1) : QString();
            const QString src = m.hasMatch() ? m.captured(2) : QString();
            QStringList figure = {QStringLiteral("\\begin{figure}[h]"), QStringLiteral("\\centering"),
                                  QStringLiteral("\\includegraphics[width=0.85\\linewidth]{%1}").arg(src)};
            if (!alt.isEmpty()) figure << QStringLiteral("\\caption{%1}").arg(inlineToLatex(alt));
            figure << QStringLiteral("\\end{figure}");
            parts << figure.join('\n');
            break;
        }
        case BlockType::Html:
            break; // HTML-блоки в LaTeX-экспорт не переносятся
        default:
            parts << inlineToLatex(text);
            break;
        }
    }
    parts << kClosing;

    QDir().mkpath(QFileInfo(outPath).absolutePath());
    QFile file(outPath);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) return false;
    file.write(parts.join(QStringLiteral("\n\n")).toUtf8());
    return true;
}

} // namespace pdftransl
