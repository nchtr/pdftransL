#include "export/html_export.h"
#include <QFile>
#include <QRegularExpression>
#include <QFileInfo>

namespace pdftransl {

static QString markdownToHtml(const QString& md) {
    QString html = md;

    // Headings
    static QRegularExpression h3(R"(^### (.+)$)", QRegularExpression::MultilineOption);
    static QRegularExpression h2(R"(^## (.+)$)", QRegularExpression::MultilineOption);
    static QRegularExpression h1(R"(^# (.+)$)", QRegularExpression::MultilineOption);
    html.replace(h3, "<h3>\\1</h3>");
    html.replace(h2, "<h2>\\1</h2>");
    html.replace(h1, "<h1>\\1</h1>");

    // Bold/italic
    static QRegularExpression bold(R"(\*\*(.+?)\*\*)");
    static QRegularExpression italic(R"(\*(.+?)\*)");
    html.replace(bold, "<strong>\\1</strong>");
    html.replace(italic, "<em>\\1</em>");

    // Display math -> KaTeX div
    static QRegularExpression displayMath(R"(\$\$([\s\S]+?)\$\$)");
    html.replace(displayMath, R"(<div class="math-display">\\[\1\\]</div>)");

    // Inline math
    static QRegularExpression inlineMath(R"(\$([^\$\n]+?)\$)");
    html.replace(inlineMath, R"(<span class="math-inline">\\(\1\\)</span>)");

    // Code blocks
    static QRegularExpression codeBlock(R"(```(\w*)\n([\s\S]*?)```)");
    html.replace(codeBlock, "<pre><code class=\"language-\\1\">\\2</code></pre>");

    // Paragraphs
    static QRegularExpression emptyLine(R"(\n{2,})");
    auto parts = html.split(emptyLine);
    for (auto& part : parts) {
        QString trimmed = part.trimmed();
        if (!trimmed.startsWith('<'))
            part = "<p>" + trimmed + "</p>";
    }
    html = parts.join("\n");

    return html;
}

void exportHtml(const QString& markdown, const std::vector<Asset>& assets,
                const QString& outputPath) {
    QString body = markdownToHtml(markdown);

    QString html = QStringLiteral(
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">\n"
        "<title>Translation</title>\n"
        "<link rel=\"stylesheet\" href=\"https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css\">\n"
        "<script defer src=\"https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js\"></script>\n"
        "<script defer src=\"https://cdn.jsdelivr.net/npm/katex@0.16/dist/contrib/auto-render.min.js\" "
        "onload=\"renderMathInElement(document.body)\"></script>\n"
        "<style>body{max-width:800px;margin:2em auto;font-family:serif;line-height:1.6}"
        "pre{background:#f5f5f5;padding:1em;overflow-x:auto}"
        ".math-display{text-align:center;margin:1em 0}</style>\n"
        "</head><body>\n%1\n</body></html>").arg(body);

    QFile f(outputPath);
    if (f.open(QIODevice::WriteOnly | QIODevice::Text))
        f.write(html.toUtf8());
}

} // namespace pdftransl
