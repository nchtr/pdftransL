#pragma once
#include <QString>
#include <QStringList>
#include <QVariantMap>
#include <QMap>
#include <QUuid>
#include <vector>

namespace pdftransl {

enum class BlockType {
    Text, Heading, Math, Code, Table, Image, Html, References
};

struct Block {
    BlockType type = BlockType::Text;
    QString text;
    int page = 0;
};

struct Asset {
    QString path;
    QString relPath;
    QString kind; // "image"
    int page = 0;
};

struct ParsedDocument {
    QString sourcePath;
    QString markdown;
    QString markdownPath;
    std::vector<Asset> assets;
    QString backend;
    QVariantMap meta;
};

struct QAIssue {
    QString level; // "error" | "warning" | "info"
    QString message;
    QString rule;
};

struct Segment {
    QString id;
    BlockType kind = BlockType::Text;
    QString sourceText;
    QString maskedText;
    QMap<QString, QString> placeholders;
    QString translation;
    bool ok = true;
    std::vector<QAIssue> issues;
    bool passthrough = false;

    QString finalText() const {
        if (passthrough) return sourceText;
        return translation.isEmpty() ? sourceText : translation;
    }
};

struct JobResult {
    QString jobId;
    QString status; // "completed" | "partial" | "failed" | "paused"
    QString outputMarkdownPath;
    QVariantMap exports; // fmt -> path
    std::vector<Segment> segments;
    QVariantMap report;
    QString reportPath;
    QString assetsDir;
    QString error;
};

inline QString newId(const QString& prefix = "id_") {
    return prefix + QUuid::createUuid().toString(QUuid::Id128).left(12);
}

} // namespace pdftransl
