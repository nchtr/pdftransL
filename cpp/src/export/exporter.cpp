#include "export/exporter.h"
#include "export/html_export.h"
#include "export/latex_export.h"
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QStandardPaths>

namespace pdftransl {

namespace {

bool pandocExport(const QString& mdPath, const QString& outPath, const QString& assetsDir) {
    const QString pandoc = QStandardPaths::findExecutable(QStringLiteral("pandoc"));
    if (pandoc.isEmpty()) return false;

    QStringList args = {mdPath, "-o", outPath, "--standalone",
                         "--from", "markdown+tex_math_dollars"};
    if (!assetsDir.isEmpty()) args << "--resource-path" << assetsDir;
    if (outPath.endsWith(QStringLiteral(".pdf"))) {
        args << "--pdf-engine=xelatex" << "-V" << "mainfont=DejaVu Serif";
    }

    QProcess proc;
    proc.start(pandoc, args);
    if (!proc.waitForFinished(600000)) {
        proc.kill();
        proc.waitForFinished(3000);
        return false;
    }
    return proc.exitCode() == 0 && QFileInfo::exists(outPath);
}

} // namespace

QVariantMap exportDocument(const QString& markdown, const QString& outBase,
                            const QStringList& formats, const QString& assetsDir,
                            const QString& title) {
    QDir().mkpath(QFileInfo(outBase).absolutePath());

    QVariantMap files;
    QVariantMap engines;

    if (formats.contains(QStringLiteral("html"))) {
        const QString htmlPath = outBase + QStringLiteral(".html");
        const bool ok = exportHtml(markdown, htmlPath, assetsDir, title);
        files["html"] = ok ? QVariant(htmlPath) : QVariant();
        engines["html"] = ok ? QStringLiteral("builtin") : QStringLiteral("unavailable: failed to write file");
    }

    if (formats.contains(QStringLiteral("latex"))) {
        const QString texPath = outBase + QStringLiteral(".tex");
        const bool ok = exportLatex(markdown, texPath, title);
        files["latex"] = ok ? QVariant(texPath) : QVariant();
        engines["latex"] = ok ? QStringLiteral("builtin") : QStringLiteral("unavailable: failed to write file");
    }

    if (formats.contains(QStringLiteral("docx")) || formats.contains(QStringLiteral("pdf"))) {
        // Временная markdown-копия — единственный вход pandoc; удаляется в конце.
        const QString mdPath = outBase + QStringLiteral(".export.md");
        QFile mdFile(mdPath);
        if (mdFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
            mdFile.write(markdown.toUtf8());
            mdFile.close();
        }

        if (formats.contains(QStringLiteral("docx"))) {
            const QString docxPath = outBase + QStringLiteral(".docx");
            if (pandocExport(mdPath, docxPath, assetsDir)) {
                files["docx"] = docxPath;
                engines["docx"] = QStringLiteral("pandoc");
            } else {
                files["docx"] = QVariant();
                engines["docx"] = QStringLiteral("unavailable: install pandoc");
            }
        }
        if (formats.contains(QStringLiteral("pdf"))) {
            const QString pdfPath = outBase + QStringLiteral(".pdf");
            if (pandocExport(mdPath, pdfPath, assetsDir)) {
                files["pdf"] = pdfPath;
                engines["pdf"] = QStringLiteral("pandoc");
            } else {
                files["pdf"] = QVariant();
                engines["pdf"] =
                    QStringLiteral("unavailable: install pandoc and a TeX engine (xelatex/tectonic)");
            }
        }

        QFile::remove(mdPath);
    }

    QVariantMap result;
    result["files"] = files;
    result["engines"] = engines;
    return result;
}

} // namespace pdftransl
