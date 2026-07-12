#include "parsing/subprocess_backend.h"
#include <QProcess>
#include <QDir>
#include <QFileInfo>
#include <QTemporaryDir>
#include <stdexcept>

namespace pdftransl {

static QStringList backendOrder(const QString& preferred) {
    QStringList all = {"marker", "nougat", "pymupdf", "docling"};
    if (preferred != "auto" && all.contains(preferred)) {
        all.removeAll(preferred);
        all.prepend(preferred);
    }
    return all;
}

static QString findTool(const QString& name) {
    QProcess which;
    which.start("which", {name});
    which.waitForFinished(5000);
    if (which.exitCode() == 0)
        return QString::fromUtf8(which.readAllStandardOutput()).trimmed();
    return {};
}

ParsedDocument parseWithSubprocess(const QString& pdfPath, const PipelineConfig& config) {
    auto backends = backendOrder(config.parserBackend);

    for (const auto& backend : backends) {
        QString tool = findTool(backend);
        if (tool.isEmpty()) continue;

        QTemporaryDir outDir;
        if (!outDir.isValid()) continue;
        outDir.setAutoRemove(false);

        QProcess proc;
        proc.setWorkingDirectory(outDir.path());

        QStringList args;
        if (backend == "marker") {
            args = {pdfPath, outDir.path()};
        } else if (backend == "nougat") {
            args = {"--pdf", pdfPath, "--out", outDir.path(), "--markdown"};
        } else if (backend == "pymupdf") {
            args = {"-m", "pymupdf", pdfPath, "--output", outDir.path()};
            tool = "python3";
        } else {
            args = {pdfPath, "--output-dir", outDir.path()};
        }

        proc.start(tool, args);
        if (!proc.waitForFinished(config.parserTimeout * 1000)) {
            proc.kill();
            continue;
        }
        if (proc.exitCode() != 0 && !config.parserFallback) {
            throw std::runtime_error(
                ("parser " + backend + " failed: " + proc.readAllStandardError()).toStdString());
        }
        if (proc.exitCode() != 0) continue;

        QDir dir(outDir.path());
        auto mdFiles = dir.entryList({"*.md"}, QDir::Files);
        if (mdFiles.isEmpty()) continue;

        QString mdPath = dir.absoluteFilePath(mdFiles.first());
        QFile f(mdPath);
        f.open(QIODevice::ReadOnly);
        QString markdown = QString::fromUtf8(f.readAll());

        ParsedDocument doc;
        doc.sourcePath = pdfPath;
        doc.markdown = markdown;
        doc.markdownPath = mdPath;
        doc.backend = backend;

        auto images = dir.entryList({"*.png", "*.jpg", "*.jpeg", "*.svg"}, QDir::Files);
        for (const auto& img : images) {
            Asset a;
            a.path = dir.absoluteFilePath(img);
            a.relPath = img;
            a.kind = "image";
            doc.assets.push_back(std::move(a));
        }
        return doc;
    }
    throw std::runtime_error("no parser backend available for: " + pdfPath.toStdString());
}

} // namespace pdftransl
