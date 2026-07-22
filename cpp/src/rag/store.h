#pragma once
// Память переводов (TM) — «обучаемое» хранилище в SQLite. Каждый удачный
// перевод сегмента сохраняется вместе с хэширующим эмбеддингом источника;
// новые документы получают точные совпадения бесплатно (человеческие
// правки приоритетнее автоматических) и похожие сегменты как few-shot
// примеры (косинусное сходство). Порт pdftransl/rag/store.py (упрощён: без
// доменного фильтра и файлового снимка-кэша — SQLite-индексов достаточно
// для десктопного масштаба одного пользователя).
#include "rag/embeddings.h"
#include <QMutex>
#include <QSqlDatabase>
#include <QString>
#include <optional>
#include <vector>

namespace pdftransl {

class TranslationMemory {
public:
    struct Match {
        QString source;
        QString target;
        double similarity = 0.0;
    };
    struct Stats {
        int segments = 0;
        int humanCorrections = 0;
    };

    explicit TranslationMemory(const QString& dbPath, int embeddingDim = 128);

    // Сохранить успешный перевод сегмента вместе с эмбеддингом источника.
    // origin: "auto" (перевод пайплайна) | "human" (ручная правка —
    // приоритетнее при exactMatch()/search() и вытесняет более старые auto-
    // записи того же источника и языковой пары).
    void add(const QString& source, const QString& target, const QString& srcLang,
             const QString& tgtLang, const QString& origin = QStringLiteral("auto"));

    // Точное совпадение источника (после trim) для данной языковой пары.
    std::optional<QString> exactMatch(const QString& source, const QString& srcLang,
                                       const QString& tgtLang) const;

    // Косинусный поиск похожих сегментов той же языковой пары; результат
    // отсортирован по (human-происхождение, сходство) по убыванию и обрезан
    // до topK.
    std::vector<Match> search(const QString& source, const QString& srcLang,
                               const QString& tgtLang, int topK = 3,
                               double minSimilarity = 0.82) const;

    Stats stats() const;

private:
    QSqlDatabase db() const; // соединение, привязанное к текущему потоку
    void ensureSchema(QSqlDatabase& conn) const;

    QString m_dbPath;
    QString m_baseConnName;
    HashingEmbedder m_embedder;
    mutable QMutex m_writeMutex;
};

} // namespace pdftransl
