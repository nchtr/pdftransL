#include "rag/glossary.h"
#include <QAtomicInteger>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QMutexLocker>
#include <QRegularExpression>
#include <QSqlQuery>
#include <QStringConverter>
#include <QTextStream>
#include <QThread>

namespace pdftransl {

namespace {
// See the identical comment in rag/store.cpp: a `this`-pointer-based name
// can collide with a destroyed instance's still-registered connection once
// the allocator reuses the address, silently handing a new Glossary the
// wrong (stale) database connection. A monotonic counter can't collide.
QAtomicInteger<quint64> g_glossaryInstanceCounter{0};
} // namespace

Glossary::Glossary(const QString& dbPath) : m_dbPath(dbPath) {
    QDir().mkpath(QFileInfo(dbPath).absolutePath());
    m_baseConnName = QStringLiteral("gl_%1").arg(g_glossaryInstanceCounter.fetchAndAddOrdered(1));
    QSqlDatabase conn = db();
    ensureSchema(conn);
}

QSqlDatabase Glossary::db() const {
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

void Glossary::ensureSchema(QSqlDatabase& conn) const {
    QSqlQuery q(conn);
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS glossary ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "term TEXT NOT NULL,"
        "translation TEXT NOT NULL,"
        "src_lang TEXT NOT NULL,"
        "tgt_lang TEXT NOT NULL,"
        "UNIQUE (term, src_lang, tgt_lang))"));
}

void Glossary::add(const QString& term, const QString& translation, const QString& srcLang,
                    const QString& tgtLang) {
    const QString t = term.trimmed();
    const QString tr = translation.trimmed();
    if (t.isEmpty() || tr.isEmpty()) return;

    QMutexLocker lock(&m_writeMutex);
    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "INSERT INTO glossary (term, translation, src_lang, tgt_lang) VALUES (?,?,?,?) "
        "ON CONFLICT (term, src_lang, tgt_lang) DO UPDATE SET translation=excluded.translation"));
    q.addBindValue(t);
    q.addBindValue(tr);
    q.addBindValue(srcLang);
    q.addBindValue(tgtLang);
    q.exec();
}

std::vector<Glossary::Match> Glossary::match(const QString& text, const QString& srcLang,
                                              const QString& tgtLang, int limit) const {
    std::vector<Match> hits;
    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "SELECT term, translation FROM glossary WHERE src_lang=? AND tgt_lang=?"));
    q.addBindValue(srcLang);
    q.addBindValue(tgtLang);
    if (!q.exec()) return hits;

    while (q.next() && static_cast<int>(hits.size()) < limit) {
        const QString term = q.value(0).toString();
        // Границы "слова" учитывают дефис: "co-" не должно ловить середину
        // "co-occurrence" частично, как в pdftransl.rag.glossary.match.
        const QRegularExpression re(
            QStringLiteral("(?<![\\w-])%1(?![\\w-])").arg(QRegularExpression::escape(term)),
            QRegularExpression::CaseInsensitiveOption);
        if (re.match(text).hasMatch()) {
            hits.push_back(Match{term, q.value(1).toString()});
        }
    }
    return hits;
}

bool Glossary::remove(const QString& term, const QString& srcLang, const QString& tgtLang) {
    QMutexLocker lock(&m_writeMutex);
    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "DELETE FROM glossary WHERE term=? AND src_lang=? AND tgt_lang=?"));
    q.addBindValue(term.trimmed());
    q.addBindValue(srcLang);
    q.addBindValue(tgtLang);
    if (!q.exec()) return false;
    return q.numRowsAffected() > 0;
}

std::vector<Glossary::Entry> Glossary::listAll(const QString& srcLang, const QString& tgtLang) const {
    std::vector<Entry> entries;
    QSqlDatabase conn = db();
    QSqlQuery q(conn);
    if (!srcLang.isEmpty() && !tgtLang.isEmpty()) {
        q.prepare(QStringLiteral(
            "SELECT term, translation, src_lang, tgt_lang FROM glossary "
            "WHERE src_lang=? AND tgt_lang=? ORDER BY term"));
        q.addBindValue(srcLang);
        q.addBindValue(tgtLang);
    } else {
        q.prepare(QStringLiteral(
            "SELECT term, translation, src_lang, tgt_lang FROM glossary ORDER BY term"));
    }
    if (!q.exec()) return entries;
    while (q.next()) {
        entries.push_back(Entry{q.value(0).toString(), q.value(1).toString(),
                                 q.value(2).toString(), q.value(3).toString()});
    }
    return entries;
}

int Glossary::loadCsv(const QString& path, const QString& srcLang, const QString& tgtLang) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) return 0;

    int count = 0;
    QTextStream in(&file);
    in.setEncoding(QStringConverter::Utf8);
    while (!in.atEnd()) {
        const QString line = in.readLine();
        if (line.trimmed().isEmpty() || line.trimmed().startsWith('#')) continue;
        const QStringList fields = line.split(',');
        if (fields.size() < 2) continue;
        add(fields[0], fields[1], srcLang, tgtLang);
        ++count;
    }
    return count;
}

} // namespace pdftransl
