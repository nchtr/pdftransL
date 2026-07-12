#include "export/exporter.h"
#include "export/html_export.h"
#include "export/latex_export.h"
#include <QDir>
#include <QFile>
#include <QProcess>

namespace pdftransl {

QVariantMap exportAll(const QString& markdown, const std::vector<Asset>& assets,
                     const PipelineConfig& config, const QString& outputDir) {
    QDir().mkpath(outputDir);
    QVariantMap paths;

    QString mdPath = outputDir + "/translation.md";
    QFile mdFile(mdPath);
    if (mdFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
        mdFile.write(markdown.toUtf8());
        paths["markdown"] = mdPath;
    }

    for (const auto& fmt : config.exportFormats) {
        if (fmt == "html") {
            QString p = outputDir + "/translation.html";
            exportHtml(markdown, assets, p);
            paths["html"] = p;
        } else if (fmt == "latex") {
            QString p = outputDir + "/translation.tex";
            exportLatex(markdown, p);
            paths["latex"] = p;
        } else if (fmt == "docx" || fmt == "pdf") {
            // DOCX/PDF export via pandoc subprocess
            QString ext = (fmt == "docx") ? ".docx" : ".pdf";
            QString p = outputDir + "/translation" + ext;
            QProcess pandoc;
            pandoc.start("pandoc", {"-f", "markdown", "-o", p, mdPath});
            if (pandoc.waitForFinished(60000) && pandoc.exitCode() == 0)
                paths[fmt] = p;
        }
    }
    return paths;
}

} // namespace pdftransl
