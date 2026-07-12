#include "rag/store.h"
#include <QSqlQuery>
#include <QSqlError>
#include <QDir>
#include <QUuid>

namespace pdftransl {

TranslationMemory::TranslationMemory(const QString& dbPath) {
    m_connName = "tm_" + QUuid::createUuid().toString(QUuid::Id128).left(8);
    QDir().mkpath(QFileInfo(dbPath).absolutePath());
    m_db = QSqlDatabase::addDatabase("QSQLITE", m_connName);
    m_db.setDatabaseName(dbPath);
    m_db.open();
    initDb();
}

TranslationMemory::~TranslationMemory() {
    m_db.close();
    QSqlDatabase::removeDatabase(m_connName);
}

void TranslationMemory::initDb() {
    QSqlQuery q(m_db);
    q.exec("CREATE TABLE IF NOT EXISTS tm ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT,"
           "source TEXT NOT NULL,"
           "translation TEXT NOT NULL,"
           "domain TEXT DEFAULT '',"
           "embedding BLOB,"
           "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)");
    q.exec("CREATE INDEX IF NOT EXISTS idx_tm_domain ON tm(domain)");
}

void TranslationMemory::store(const QString& source, const QString& translation,
                              const QString& domain) {
    QMutexLocker lock(&m_mutex);
    auto emb = m_embedder.embed(source);
    QByteArray blob(reinterpret_cast<const char*>(emb.data()), emb.size() * sizeof(float));

    QSqlQuery q(m_db);
    q.prepare("INSERT INTO tm (source, translation, domain, embedding) VALUES (?, ?, ?, ?)");
    q.addBindValue(source);
    q.addBindValue(translation);
    q.addBindValue(domain);
    q.addBindValue(blob);
    q.exec();
}

std::vector<TMEntry> TranslationMemory::search(const QString& query, int topK,
                                               double minSim, const QString& domain) {
    QMutexLocker lock(&m_mutex);
    auto queryEmb = m_embedder.embed(query);

    QSqlQuery q(m_db);
    if (domain.isEmpty())
        q.exec("SELECT source, translation, domain, embedding FROM tm");
    else {
        q.prepare("SELECT source, translation, domain, embedding FROM tm WHERE domain = ?");
        q.addBindValue(domain);
        q.exec();
    }

    std::vector<TMEntry> candidates;
    while (q.next()) {
        QByteArray blob = q.value(3).toByteArray();
        if (blob.size() != queryEmb.size() * static_cast<int>(sizeof(float))) continue;
        QVector<float> emb(queryEmb.size());
        memcpy(emb.data(), blob.data(), blob.size());

        float sim = m_embedder.similarity(queryEmb, emb);
        if (sim >= minSim) {
            TMEntry e;
            e.source = q.value(0).toString();
            e.translation = q.value(1).toString();
            e.domain = q.value(2).toString();
            e.score = sim;
            candidates.push_back(std::move(e));
        }
    }

    std::sort(candidates.begin(), candidates.end(),
              [](const TMEntry& a, const TMEntry& b) { return a.score > b.score; });
    if (static_cast<int>(candidates.size()) > topK)
        candidates.resize(topK);
    return candidates;
}

} // namespace pdftransl
