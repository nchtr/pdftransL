#include "parsing/subprocess_backend.h"
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QStandardPaths>
#include <stdexcept>

namespace pdftransl {

namespace {

const QStringList kImageExtensions = {"*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.webp", "*.svg"};

QStringList substitutePlaceholders(const QStringList& tokens, const QString& input,
                                    const QString& output) {
    QStringList out;
    out.reserve(tokens.size());
    for (QString token : tokens) {
        token.replace(QStringLiteral("{input}"), input);
        token.replace(QStringLiteral("{output}"), output);
        out << token;
    }
    return out;
}

// Первый файл (по алфавиту), подходящий под первый непустой шаблон из
// globs — шаблоны пробуются по порядку (nougat, например, отдаёт
// предпочтение .mmd перед .md).
QString findMarkdown(const QString& workdir, const QStringList& globs) {
    for (const QString& glob : globs) {
        QDirIterator it(workdir, {glob}, QDir::Files, QDirIterator::Subdirectories);
        QStringList matches;
        while (it.hasNext()) matches << it.next();
        if (!matches.isEmpty()) {
            matches.sort();
            return matches.first();
        }
    }
    return {};
}

std::vector<Asset> collectAssets(const QString& dir) {
    std::vector<Asset> assets;
    QDir base(dir);
    QDirIterator it(dir, kImageExtensions, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) {
        const QString path = it.next();
        Asset asset;
        asset.path = path;
        asset.relPath = base.relativeFilePath(path);
        asset.kind = QStringLiteral("image");
        assets.push_back(std::move(asset));
    }
    return assets;
}

} // namespace

SubprocessBackendSpec markerBackendSpec() {
    SubprocessBackendSpec spec;
    spec.name = QStringLiteral("marker");
    spec.executable = QStringLiteral("marker_single");
    spec.argsTemplate = {"{input}", "--output_dir", "{output}"};
    spec.markdownGlobs = {"*.md"};
    return spec;
}

SubprocessBackendSpec nougatBackendSpec() {
    SubprocessBackendSpec spec;
    spec.name = QStringLiteral("nougat");
    spec.executable = QStringLiteral("nougat");
    spec.argsTemplate = {"{input}", "-o", "{output}", "--markdown"};
    // .mmd в приоритете перед .md — так nougat_backend.py искал результат.
    spec.markdownGlobs = {"*.mmd", "*.md"};
    return spec;
}

SubprocessBackendSpec doclingBackendSpec() {
    SubprocessBackendSpec spec;
    spec.name = QStringLiteral("docling");
    spec.executable = QStringLiteral("docling");
    spec.argsTemplate = {"{input}", "--to", "md", "--output", "{output}"};
    spec.markdownGlobs = {"*.md"};
    return spec;
}

SubprocessBackend::SubprocessBackend(SubprocessBackendSpec spec) : m_spec(std::move(spec)) {}

QString SubprocessBackend::name() const { return m_spec.name; }

bool SubprocessBackend::available() const {
    return !QStandardPaths::findExecutable(m_spec.executable).isEmpty();
}

ParsedDocument SubprocessBackend::parse(const QString& pdfPath, const QString& workdir) {
    if (!QFileInfo::exists(pdfPath)) {
        throw std::runtime_error(("PDF not found: " + pdfPath).toStdString());
    }
    QDir().mkpath(workdir);

    const QString exe = QStandardPaths::findExecutable(m_spec.executable);
    if (exe.isEmpty()) {
        throw std::runtime_error(
            (m_spec.name + ": executable '" + m_spec.executable + "' not found in PATH")
                .toStdString());
    }

    QProcess process;
    process.setWorkingDirectory(workdir);
    process.start(exe, substitutePlaceholders(m_spec.argsTemplate, pdfPath, workdir));
    if (!process.waitForStarted(10000)) {
        throw std::runtime_error((m_spec.name + ": failed to start").toStdString());
    }
    if (!process.waitForFinished(m_spec.timeoutSeconds * 1000)) {
        process.kill();
        process.waitForFinished(3000);
        throw std::runtime_error(
            QStringLiteral("%1 timed out after %2s").arg(m_spec.name).arg(m_spec.timeoutSeconds)
                .toStdString());
    }
    if (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0) {
        const QString stderrTail = QString::fromUtf8(process.readAllStandardError()).right(1500);
        throw std::runtime_error(
            QStringLiteral("%1 failed (exit %2): %3")
                .arg(m_spec.name)
                .arg(process.exitCode())
                .arg(stderrTail)
                .toStdString());
    }

    const QString mdPath = findMarkdown(workdir, m_spec.markdownGlobs);
    if (mdPath.isEmpty()) {
        throw std::runtime_error((m_spec.name + " produced no markdown under " + workdir).toStdString());
    }
    QFile file(mdPath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        throw std::runtime_error(("cannot read " + mdPath).toStdString());
    }
    const QString markdown = QString::fromUtf8(file.readAll());

    ParsedDocument doc;
    doc.sourcePath = pdfPath;
    doc.markdown = markdown;
    doc.markdownPath = mdPath;
    doc.backend = m_spec.name;
    doc.assets = collectAssets(QFileInfo(mdPath).absolutePath());
    return doc;
}

} // namespace pdftransl
