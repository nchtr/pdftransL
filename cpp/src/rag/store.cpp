#include "rag/store.h"
#include <QAtomicInteger>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMutexLocker>
#include <QSqlQuery>
#include <QThread>
#include <algorithm>

namespace pdftransl {

namespace {

QString hashOf(const QString& text) {
    return QString::fromUtf8(
        QCryptographicHash::hash(text.trimmed().toUtf8(), QCryptographicHash::Sha256).toHex());
}

QByteArray vectorToJson(const std::vector<float>& v) {
    QJsonArray arr;
    for (float f : v) arr.append(static_cast<double>(f));
    return QJsonDocument(arr).toJson(QJsonDocument::Compact);
}

std::vector<float> vectorFromJson(const QByteArray& json) {
    std::vector<float> v;
    QJsonDocument doc = QJsonDocument::fromJson(json);
    if (!doc.isArray()) return v;
    const QJsonArray arr = doc.array();
    v.reserve(arr.size());
    for (const auto& val : arr) v.push_back(static_cast<float>(val.toDouble()));
    return v;
}

// Monotonic counter for connection-name uniqueness. Using the `this`
// pointer alone would collide once an earlier TranslationMemory is
// destroyed and a new one happens to be allocated at the same address —
// QSqlDatabase connections are never implicitly closed when their owning
// object dies, so a stale connection (pointing at a *different* db file)
// would silently be picked up by db() for the new instance.
QAtomicInteger<quint64> g_tmInstanceCounter{0};

} // namespace

TranslationMemory::TranslationMemory(const QString& dbPath, int embeddingDim)
    : m_dbPath(dbPath), m_embedder(embeddingDim) {
    QDir().mkpath(QFileInfo(dbPath).absolutePath());
    m_baseConnName = QStringLiteral("tm_%1").arg(g_tmInstanceCounter.fetchAndAddOrdered(1));
    QSqlDatabase conn = db();
    ensureSchema(conn);
}

QSqlDatabase TranslationMemory::db() const {
    // SQLite/QSqlDatabase connections are not safe to share across threads;
    // each thread that touches this TM gets its own lazily-opened connection.
    const QString connName = QStringLiteral("%1_t%2")
                                  .arg(m_baseConnName)
                                  .arg(reinterpret_cast<quintptr>(QThread::currentThread()));
    if (QSqlDatabase::contains(connName)) {
        QSqlDatabase existing = QSqlDatabase::database(connName);
        if (existing.isOpen()) return existing;
    }
    QSqlDatabase conn = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connName);
    conn.setDatabaseName(m_dbPath);
    conn.open();
    QSqlQuery(conn).exec(QStringLiteral("PRAGMA busy_timeout=5000"));
    return conn;
}

void TranslationMemory::ensureSchema(QSqlDatabase& conn) const {
    QSqlQuery q(conn);
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS tm_segments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "source_hash TEXT NOT NULL,"
        "source TEXT NOT NULL,"
        "target TEXT NOT NULL,"
        "src_lang TEXT NOT NULL,"
        "tgt_lang TEXT NOT NULL,"
        "origin TEXT NOT NULL DEFAULT 'auto',"
        "embedding BLOB,"
        "created_at REAL NOT NULL)"));
    q.exec(QStringLiteral(
        "CREATE INDEX IF NOT EXISTS idx_tm_hash ON tm_segments (source_hash, src_lang, tgt_lang)"));
    q.exec(QStringLiteral("CREATE INDEX IF NOT EXISTS idx_tm_langs ON tm_segments (src_lang, tgt_lang)"));
}

void TranslationMemory::add(const QString& source, const QString& target, const QString& srcLang,
                             const QString& tgtLang, const QString& origin) {
    const QString src = source.trimmed();
    const QString tgt = target.trimmed();
    if (src.isEmpty() || tgt.isEmpty()) return;

    const std::vector<float> vector = m_embedder.embed(src);

    QMutexLocker lock(&m_writeMutex);
    QSqlDatabase conn = db();

    if (origin == QStringLiteral("human")) {
        // Человеческие правки заменяют более старые auto-записи того же
        // источника и языковой пары.
        QSqlQuery del(conn);
        del.prepare(QStringLiteral(
            "DELETE FROM tm_segments WHERE source_hash=? AND src_lang=? AND tgt_lang=? AND origin='auto'"));
        del.addBindValue(hashOf(src));
        del.addBindValue(srcLang);
        del.addBindValue(tgtLang);
        del.exec();
    }

    QSqlQuery ins(conn);
    ins.prepare(QStringLiteral(
        "INSERT INTO tm_segments (source_hash, source, target, src_lang, tgt_lang, origin, "
        "embedding, created_at) VALUES (?,?,?,?,?,?,?,?)"));
    ins.addBindValue(hashOf(src));
    ins.addBindValue(src);
    ins.addBindValue(tgt);
    ins.addBindValue(srcLang);
    ins.addBindValue(tgtLang);
    ins.addBindValue(origin);
    ins.addBindValue(vectorToJson(vector));
    ins.addBindValue(static_cast<qint64>(QDateTime::currentSecsSinceEpoch()));
    ins.exec();
}

std::optional<QString> TranslationMemory::exactMatch(const QString& source, const QString& srcLang,
                                                       const QString& tgtLang) const {
    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "SELECT target FROM tm_segments WHERE source_hash=? AND src_lang=? AND tgt_lang=? "
        "ORDER BY (origin='human') DESC, created_at DESC LIMIT 1"));
    q.addBindValue(hashOf(source));
    q.addBindValue(srcLang);
    q.addBindValue(tgtLang);
    if (q.exec() && q.next()) return q.value(0).toString();
    return std::nullopt;
}

std::vector<TranslationMemory::Match> TranslationMemory::search(const QString& source,
                                                                  const QString& srcLang,
                                                                  const QString& tgtLang, int topK,
                                                                  double minSimilarity) const {
    const std::vector<float> queryVec = m_embedder.embed(source);

    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "SELECT source, target, origin, embedding FROM tm_segments WHERE src_lang=? AND tgt_lang=?"));
    q.addBindValue(srcLang);
    q.addBindValue(tgtLang);

    std::vector<Match> results;
    if (!q.exec()) return results;

    struct Candidate {
        QString source, target;
        bool human;
        double similarity;
    };
    std::vector<Candidate> candidates;
    while (q.next()) {
        const std::vector<float> vec = vectorFromJson(q.value(3).toByteArray());
        if (vec.empty()) continue;
        const double sim = cosine(queryVec, vec);
        if (sim >= minSimilarity) {
            candidates.push_back({q.value(0).toString(), q.value(1).toString(),
                                   q.value(2).toString() == QStringLiteral("human"), sim});
        }
    }
    std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
        if (a.human != b.human) return a.human;
        return a.similarity > b.similarity;
    });
    if (static_cast<int>(candidates.size()) > topK) candidates.resize(static_cast<size_t>(topK));

    results.reserve(candidates.size());
    for (const auto& c : candidates) results.push_back(Match{c.source, c.target, c.similarity});
    return results;
}

TranslationMemory::Stats TranslationMemory::stats() const {
    QSqlDatabase conn = db();
    Stats stats;
    QSqlQuery q1(QStringLiteral("SELECT COUNT(*) FROM tm_segments"), conn);
    if (q1.next()) stats.segments = q1.value(0).toInt();
    QSqlQuery q2(conn);
    q2.exec(QStringLiteral("SELECT COUNT(*) FROM tm_segments WHERE origin='human'"));
    if (q2.next()) stats.humanCorrections = q2.value(0).toInt();
    return stats;
}

} // namespace pdftransl
