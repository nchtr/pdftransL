#include "translation/checkpoint.h"
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>
#include <QDir>

namespace pdftransl {

Checkpoint::Checkpoint(const QString& path) : m_path(path) {}

void Checkpoint::save(int order, const Segment& segment) {
    QMutexLocker lock(&m_mutex);
    QDir().mkpath(QFileInfo(m_path).absolutePath());
    QFile f(m_path);
    if (!f.open(QIODevice::Append | QIODevice::Text)) return;
    QJsonObject obj;
    obj["order"] = order;
    obj["id"] = segment.id;
    obj["kind"] = static_cast<int>(segment.kind);
    obj["source"] = segment.sourceText;
    obj["masked"] = segment.maskedText;
    obj["translation"] = segment.translation;
    obj["ok"] = segment.ok;
    obj["passthrough"] = segment.passthrough;
    f.write(QJsonDocument(obj).toJson(QJsonDocument::Compact));
    f.write("\n");
}

QMap<int, Segment> Checkpoint::load() {
    QMutexLocker lock(&m_mutex);
    QMap<int, Segment> result;
    QFile f(m_path);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return result;
    QTextStream in(&f);
    while (!in.atEnd()) {
        auto line = in.readLine().trimmed();
        if (line.isEmpty()) continue;
        auto doc = QJsonDocument::fromJson(line.toUtf8());
        auto obj = doc.object();
        Segment seg;
        seg.id = obj["id"].toString();
        seg.kind = static_cast<BlockType>(obj["kind"].toInt());
        seg.sourceText = obj["source"].toString();
        seg.maskedText = obj["masked"].toString();
        seg.translation = obj["translation"].toString();
        seg.ok = obj["ok"].toBool(true);
        seg.passthrough = obj["passthrough"].toBool(false);
        result[obj["order"].toInt()] = seg;
    }
    return result;
}

void Checkpoint::clear() {
    QMutexLocker lock(&m_mutex);
    QFile::remove(m_path);
}

} // namespace pdftransl
