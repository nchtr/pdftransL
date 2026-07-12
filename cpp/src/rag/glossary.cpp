#include "rag/glossary.h"
#include <QSqlQuery>
#include <QDir>
#include <QUuid>

namespace pdftransl {

Glossary::Glossary(const QString& dbPath) {
    m_connName = "gl_" + QUuid::createUuid().toString(QUuid::Id128).left(8);
    QDir().mkpath(QFileInfo(dbPath).absolutePath());
    m_db = QSqlDatabase::addDatabase("QSQLITE", m_connName);
    m_db.setDatabaseName(dbPath);
    m_db.open();
    initDb();
}

Glossary::~Glossary() {
    m_db.close();
    QSqlDatabase::removeDatabase(m_connName);
}

void Glossary::initDb() {
    QSqlQuery q(m_db);
    q.exec("CREATE TABLE IF NOT EXISTS glossary ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT,"
           "term TEXT NOT NULL,"
           "translation TEXT NOT NULL,"
           "domain TEXT DEFAULT '',"
           "UNIQUE(term, domain))");
}

void Glossary::add(const QString& term, const QString& translation, const QString& domain) {
    QMutexLocker lock(&m_mutex);
    QSqlQuery q(m_db);
    q.prepare("INSERT OR REPLACE INTO glossary (term, translation, domain) VALUES (?, ?, ?)");
    q.addBindValue(term);
    q.addBindValue(translation);
    q.addBindValue(domain);
    q.exec();
}

void Glossary::remove(const QString& term, const QString& domain) {
    QMutexLocker lock(&m_mutex);
    QSqlQuery q(m_db);
    q.prepare("DELETE FROM glossary WHERE term = ? AND domain = ?");
    q.addBindValue(term);
    q.addBindValue(domain);
    q.exec();
}

QList<GlossaryEntry> Glossary::all(const QString& domain) {
    QMutexLocker lock(&m_mutex);
    QSqlQuery q(m_db);
    if (domain.isEmpty())
        q.exec("SELECT term, translation, domain FROM glossary ORDER BY term");
    else {
        q.prepare("SELECT term, translation, domain FROM glossary WHERE domain = ? ORDER BY term");
        q.addBindValue(domain);
        q.exec();
    }
    QList<GlossaryEntry> result;
    while (q.next()) {
        result.append({q.value(0).toString(), q.value(1).toString(), q.value(2).toString()});
    }
    return result;
}

QStringList Glossary::lookup(const QString& text, const QString& domain) {
    QMutexLocker lock(&m_mutex);
    QSqlQuery q(m_db);
    if (domain.isEmpty())
        q.exec("SELECT term, translation FROM glossary");
    else {
        q.prepare("SELECT term, translation FROM glossary WHERE domain = ? OR domain = ''");
        q.addBindValue(domain);
        q.exec();
    }
    QStringList hints;
    while (q.next()) {
        QString term = q.value(0).toString();
        if (text.contains(term, Qt::CaseInsensitive)) {
            hints.append(term + " → " + q.value(1).toString());
        }
    }
    return hints;
}

} // namespace pdftransl
