#include "export/latex_export.h"
#include <QFile>
#include <QRegularExpression>

namespace pdftransl {

static QString markdownToLatex(const QString& md) {
    QString tex = md;

    static QRegularExpression h1(R"(^# (.+)$)", QRegularExpression::MultilineOption);
    static QRegularExpression h2(R"(^## (.+)$)", QRegularExpression::MultilineOption);
    static QRegularExpression h3(R"(^### (.+)$)", QRegularExpression::MultilineOption);
    tex.replace(h1, "\\section{\\1}");
    tex.replace(h2, "\\subsection{\\1}");
    tex.replace(h3, "\\subsubsection{\\1}");

    static QRegularExpression bold(R"(\*\*(.+?)\*\*)");
    static QRegularExpression italic(R"(\*(.+?)\*)");
    tex.replace(bold, "\\textbf{\\1}");
    tex.replace(italic, "\\textit{\\1}");

    static QRegularExpression codeBlock(R"(```\w*\n([\s\S]*?)```)");
    tex.replace(codeBlock, "\\begin{verbatim}\n\\1\\end{verbatim}");

    static QRegularExpression image(R"(\!\[([^\]]*)\]\(([^\)]+)\))");
    tex.replace(image, "\\begin{figure}[h]\n\\includegraphics{\\2}\n\\caption{\\1}\n\\end{figure}");

    return tex;
}

void exportLatex(const QString& markdown, const QString& outputPath) {
    QString body = markdownToLatex(markdown);

    QString doc = QStringLiteral(
        "\\documentclass{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage{amsmath,amssymb}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage[T2A]{fontenc}\n"
        "\\usepackage[russian,english]{babel}\n\n"
        "\\begin{document}\n\n%1\n\n\\end{document}\n").arg(body);

    QFile f(outputPath);
    if (f.open(QIODevice::WriteOnly | QIODevice::Text))
        f.write(doc.toUtf8());
}

} // namespace pdftransl
