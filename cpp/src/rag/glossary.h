#pragma once
// Глоссарий терминов в SQLite: принудительные переводы терминологии
// (ручные записи или CSV-импорт), подмешиваемые в системный промпт
// перевода, когда термин встречается в конкретном сегменте. Порт
// pdftransl/rag/glossary.py.
#include <QMutex>
#include <QSqlDatabase>
#include <QString>
#include <vector>

namespace pdftransl {

class Glossary {
public:
    struct Match {
        QString term;
        QString translation;
    };
    struct Entry {
        QString term;
        QString translation;
        QString srcLang;
        QString tgtLang;
    };

    explicit Glossary(const QString& dbPath);

    // Добавить/обновить термин (upsert по term+srcLang+tgtLang).
    void add(const QString& term, const QString& translation, const QString& srcLang,
             const QString& tgtLang);

    // Термины данной языковой пары, встречающиеся в тексте целым словом
    // (регистронезависимо), не более limit штук.
    std::vector<Match> match(const QString& text, const QString& srcLang, const QString& tgtLang,
                              int limit = 30) const;

    // Удалить термин; true, если что-то удалено.
    bool remove(const QString& term, const QString& srcLang, const QString& tgtLang);

    // Все записи, опционально отфильтрованные по языковой паре (обе строки
    // пустые -> весь глоссарий).
    std::vector<Entry> listAll(const QString& srcLang = {}, const QString& tgtLang = {}) const;

    // Импорт строк "term,translation[,...]" из CSV-файла; строки-комментарии
    // (начинающиеся с '#') и с менее чем двумя полями пропускаются. Возвращает
    // число импортированных терминов.
    int loadCsv(const QString& path, const QString& srcLang, const QString& tgtLang);

private:
    QSqlDatabase db() const; // соединение, привязанное к текущему потоку
    void ensureSchema(QSqlDatabase& conn) const;

    QString m_dbPath;
    QString m_baseConnName;
    mutable QMutex m_writeMutex;
};

} // namespace pdftransl
