#pragma once
#include <QString>
#include <QStringList>
#include <QSqlDatabase>
#include <QMutex>
#include <QMap>

namespace pdftransl {

struct GlossaryEntry {
    QString term;
    QString translation;
    QString domain;
};

class Glossary {
public:
    explicit Glossary(const QString& dbPath);
    ~Glossary();

    void add(const QString& term, const QString& translation, const QString& domain = {});
    void remove(const QString& term, const QString& domain = {});
    QList<GlossaryEntry> all(const QString& domain = {});
    QStringList lookup(const QString& text, const QString& domain = {});

private:
    void initDb();
    QSqlDatabase m_db;
    QMutex m_mutex;
    QString m_connName;
};

} // namespace pdftransl
