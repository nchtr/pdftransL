#pragma once
#include "rag/embeddings.h"
#include <QString>
#include <QSqlDatabase>
#include <QMutex>
#include <vector>

namespace pdftransl {

struct TMEntry {
    QString source;
    QString translation;
    QString domain;
    float score = 0;
};

class TranslationMemory {
public:
    explicit TranslationMemory(const QString& dbPath);
    ~TranslationMemory();

    void store(const QString& source, const QString& translation, const QString& domain = {});
    std::vector<TMEntry> search(const QString& query, int topK = 3, double minSim = 0.82,
                                const QString& domain = {});

private:
    void initDb();
    QSqlDatabase m_db;
    HashingEmbedder m_embedder;
    QMutex m_mutex;
    QString m_connName;
};

} // namespace pdftransl
