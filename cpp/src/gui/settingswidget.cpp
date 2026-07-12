#include "gui/settingswidget.h"
#include "core/config.h"
#include <QComboBox>
#include <QDateTime>
#include <QDir>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QSqlDatabase>
#include <QSqlQuery>
#include <QVBoxLayout>
#include <algorithm>

namespace pdftransl {

namespace {

// (ключ настройки, заголовок) — зеркало BOOL_OPTIONS из SettingsPanel.jsx.
const QVector<QPair<QString, QString>>& boolOptions() {
    static const QVector<QPair<QString, QString>> options = {
        {"review", QObject::tr("LLM-ревью проблемных сегментов")},
        {"use_rag", QObject::tr("Память переводов / RAG")},
        {"learn", QObject::tr("Пополнять память переводов")},
        {"doc_summary", QObject::tr("Саммари документа в промпте")},
        {"auto_glossary", QObject::tr("Авто-глоссарий документа")},
        {"skip_references", QObject::tr("Не переводить список литературы")},
        {"ocr_on_scan", QObject::tr("Авто-OCR для сканов/битых PDF")},
        {"parser_fallback", QObject::tr("Фолбэк парсеров при сбое")},
        {"adaptive_throttle", QObject::tr("Пауза всех потоков при 429")},
        {"fix_latex", QObject::tr("LLM-починка битых формул")},
        {"quality_score", QObject::tr("Оценка качества LLM-судьёй")},
        {"bilingual", QObject::tr("Двуязычный документ")},
        {"describe_figures", QObject::tr("VLM-описания рисунков")},
        {"parse_cache", QObject::tr("Кэш парсинга")},
    };
    return options;
}

QSqlDatabase settingsDb(const QString& path) {
    const QString connName = QStringLiteral("settings_conn");
    QSqlDatabase conn = QSqlDatabase::contains(connName)
                             ? QSqlDatabase::database(connName)
                             : QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connName);
    if (!conn.isOpen()) {
        // Каталог базы может ещё не существовать при первом запуске (эта
        // виджет-панель может открыться раньше rag::Glossary/TranslationMemory,
        // которые сами создают его) — без mkpath open() тихо проваливался бы,
        // и все последующие запросы падали с "database not open".
        QDir().mkpath(QFileInfo(path).absolutePath());
        conn.setDatabaseName(path);
        conn.open();
        QSqlQuery(conn).exec(
            QStringLiteral("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)"));
    }
    return conn;
}

QMap<QString, QString> loadSettingsMap(const QString& path) {
    QMap<QString, QString> result;
    QSqlDatabase conn = settingsDb(path);
    QSqlQuery q(QStringLiteral("SELECT key, value FROM app_settings"), conn);
    while (q.next()) result.insert(q.value(0).toString(), q.value(1).toString());
    return result;
}

void setSetting(QSqlDatabase& conn, const QString& key, const QString& value) {
    if (value.isEmpty()) {
        QSqlQuery del(conn);
        del.prepare(QStringLiteral("DELETE FROM app_settings WHERE key=?"));
        del.addBindValue(key);
        del.exec();
        return;
    }
    QSqlQuery q(conn);
    q.prepare(QStringLiteral(
        "INSERT INTO app_settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"));
    q.addBindValue(key);
    q.addBindValue(value);
    q.exec();
}

} // namespace

QString SettingsWidget::settingsDbPath() const {
    // Настройки живут в той же базе, что и остальное состояние приложения
    // (память переводов, глоссарий), чтобы не плодить лишние файлы; путь
    // читается из окружения (PDFTRANSL_DB), как и в остальном приложении.
    return PipelineConfig::fromEnv().dbPath;
}

SettingsWidget::SettingsWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);

    auto* hint = new QLabel(
        tr("⚙ Эти значения становятся умолчаниями для всех новых задач сразу после "
           "сохранения. Пустое поле или «—» — использовать значение по умолчанию. "
           "Параметры, выбранные в форме перевода, по-прежнему приоритетнее."),
        this);
    hint->setWordWrap(true);
    hint->setStyleSheet("color: palette(mid);");
    layout->addWidget(hint);

    auto* form = new QFormLayout();

    m_provider = new QComboBox(this);
    m_provider->addItem(tr("— (из env)"), QString());
    for (const QString& p :
         {"openrouter", "anthropic", "openai", "deepseek", "ollama", "vllm", "lmstudio"}) {
        m_provider->addItem(p, p);
    }
    form->addRow(tr("Провайдер по умолчанию"), m_provider);

    m_model = new QLineEdit(this);
    m_model->setPlaceholderText(tr("например gemma3:12b"));
    form->addRow(tr("Модель"), m_model);

    m_visionModel = new QLineEdit(this);
    m_visionModel->setPlaceholderText(tr("например qwen2.5-vl"));
    form->addRow(tr("Vision-модель (OCR/рисунки)"), m_visionModel);

    m_parser = new QComboBox(this);
    m_parser->addItem(tr("— auto"), QString());
    for (const QString& b : {"auto", "marker", "nougat", "docling"}) m_parser->addItem(b, b);
    form->addRow(tr("Парсер"), m_parser);

    m_maxWorkers = new QSpinBox(this);
    m_maxWorkers->setRange(0, 64);
    m_maxWorkers->setSpecialValueText(tr("— (по умолчанию)"));
    form->addRow(tr("Параллельных переводов"), m_maxWorkers);

    m_rpmLimit = new QLineEdit(this);
    m_rpmLimit->setPlaceholderText(tr("без лимита"));
    form->addRow(tr("Лимит запросов/мин"), m_rpmLimit);

    m_formats = new QLineEdit(this);
    m_formats->setPlaceholderText(tr("html,docx,pdf"));
    form->addRow(tr("Форматы (через запятую)"), m_formats);

    m_fallbackProviders = new QLineEdit(this);
    m_fallbackProviders->setPlaceholderText(tr("например openrouter"));
    form->addRow(tr("Fallback-провайдеры"), m_fallbackProviders);

    layout->addLayout(form);

    auto* boolBox = new QGroupBox(tr("Поведение пайплайна (— = умолчание)"), this);
    auto* boolForm = new QFormLayout(boolBox);
    for (const auto& option : boolOptions()) {
        auto* combo = new QComboBox(boolBox);
        combo->addItem(QStringLiteral("—"), QString());
        combo->addItem(tr("вкл"), "true");
        combo->addItem(tr("выкл"), "false");
        boolForm->addRow(option.second, combo);
        m_triStates.insert(option.first, combo);
    }
    layout->addWidget(boolBox);

    m_saveBtn = new QPushButton(tr("Сохранить настройки"), this);
    connect(m_saveBtn, &QPushButton::clicked, this, &SettingsWidget::save);
    layout->addWidget(m_saveBtn);

    m_savedLabel = new QLabel(this);
    m_savedLabel->setStyleSheet("color: palette(mid);");
    layout->addWidget(m_savedLabel);

    layout->addStretch();
    load();
}

QComboBox* SettingsWidget::addTriState(QVBoxLayout*, const QString&, const QString&) {
    return nullptr; // не используется: тристейт-строки собираются в конструкторе через QFormLayout
}

void SettingsWidget::load() {
    const QMap<QString, QString> stored = loadSettingsMap(settingsDbPath());

    m_provider->setCurrentIndex(std::max(0, m_provider->findData(stored.value("provider"))));
    m_model->setText(stored.value("model"));
    m_visionModel->setText(stored.value("vision_model"));
    m_parser->setCurrentIndex(std::max(0, m_parser->findData(stored.value("parser_backend"))));
    m_maxWorkers->setValue(stored.value("max_workers").toInt());
    m_rpmLimit->setText(stored.value("rpm_limit"));
    m_formats->setText(stored.value("formats"));
    m_fallbackProviders->setText(stored.value("fallback_providers"));

    for (auto it = m_triStates.constBegin(); it != m_triStates.constEnd(); ++it) {
        const int idx = it.value()->findData(stored.value(it.key()));
        it.value()->setCurrentIndex(idx >= 0 ? idx : 0);
    }
}

void SettingsWidget::save() {
    QSqlDatabase conn = settingsDb(settingsDbPath());

    setSetting(conn, "provider", m_provider->currentData().toString());
    setSetting(conn, "model", m_model->text().trimmed());
    setSetting(conn, "vision_model", m_visionModel->text().trimmed());
    setSetting(conn, "parser_backend", m_parser->currentData().toString());
    setSetting(conn, "max_workers",
               m_maxWorkers->value() > 0 ? QString::number(m_maxWorkers->value()) : QString());
    setSetting(conn, "rpm_limit", m_rpmLimit->text().trimmed());
    setSetting(conn, "formats", m_formats->text().trimmed());
    setSetting(conn, "fallback_providers", m_fallbackProviders->text().trimmed());

    for (auto it = m_triStates.constBegin(); it != m_triStates.constEnd(); ++it) {
        setSetting(conn, it.key(), it.value()->currentData().toString());
    }

    m_savedLabel->setText(tr("Сохранено %1 — действует для новых задач")
                               .arg(QDateTime::currentDateTime().toString("HH:mm:ss")));
}

} // namespace pdftransl
