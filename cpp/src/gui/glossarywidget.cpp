#include "gui/glossarywidget.h"
#include "core/config.h"
#include "rag/glossary.h"
#include <QFileDialog>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QTableWidget>
#include <QVBoxLayout>

namespace pdftransl {

GlossaryWidget::GlossaryWidget(QWidget* parent) : QWidget(parent) {
    // Тот же файл БД, что и остальное состояние приложения; путь читается
    // из окружения (PDFTRANSL_DB), как и в остальном приложении.
    m_glossary = std::make_unique<Glossary>(PipelineConfig::fromEnv().dbPath);

    auto* layout = new QVBoxLayout(this);

    m_hintLabel = new QLabel(
        tr("📖 Термины глоссария принудительно подставляются в промпт перевода — так "
           "терминология остаётся единой во всех документах."),
        this);
    m_hintLabel->setWordWrap(true);
    m_hintLabel->setStyleSheet("color: palette(mid);");
    layout->addWidget(m_hintLabel);

    auto* addRow = new QHBoxLayout();
    m_termEdit = new QLineEdit(this);
    m_termEdit->setPlaceholderText(tr("термин (attention head)"));
    addRow->addWidget(m_termEdit, 2);
    m_translationEdit = new QLineEdit(this);
    m_translationEdit->setPlaceholderText(tr("перевод (головка внимания)"));
    addRow->addWidget(m_translationEdit, 2);
    m_srcLangEdit = new QLineEdit(this);
    m_srcLangEdit->setText("en");
    m_srcLangEdit->setMaximumWidth(48);
    addRow->addWidget(m_srcLangEdit);
    addRow->addWidget(new QLabel(QStringLiteral("→"), this));
    m_tgtLangEdit = new QLineEdit(this);
    m_tgtLangEdit->setText("ru");
    m_tgtLangEdit->setMaximumWidth(48);
    addRow->addWidget(m_tgtLangEdit);
    m_addBtn = new QPushButton(tr("Добавить"), this);
    connect(m_addBtn, &QPushButton::clicked, this, &GlossaryWidget::addTerm);
    addRow->addWidget(m_addBtn);
    layout->addLayout(addRow);

    m_table = new QTableWidget(this);
    m_table->setColumnCount(4);
    m_table->setHorizontalHeaderLabels(
        {tr("Термин"), tr("Перевод"), tr("Языки"), QString()});
    m_table->horizontalHeader()->setStretchLastSection(true);
    m_table->setSelectionBehavior(QAbstractItemView::SelectRows);
    m_table->setEditTriggers(QAbstractItemView::NoEditTriggers);
    layout->addWidget(m_table, 1);

    auto* bottomRow = new QHBoxLayout();
    m_removeBtn = new QPushButton(tr("Удалить выбранные"), this);
    connect(m_removeBtn, &QPushButton::clicked, this, &GlossaryWidget::removeSelected);
    bottomRow->addWidget(m_removeBtn);
    m_importBtn = new QPushButton(tr("Импорт CSV…"), this);
    connect(m_importBtn, &QPushButton::clicked, this, &GlossaryWidget::importCsv);
    bottomRow->addWidget(m_importBtn);
    bottomRow->addStretch();
    layout->addLayout(bottomRow);

    reload();
}

GlossaryWidget::~GlossaryWidget() = default;

void GlossaryWidget::addTerm() {
    const QString term = m_termEdit->text().trimmed();
    const QString translation = m_translationEdit->text().trimmed();
    const QString srcLang = m_srcLangEdit->text().trimmed();
    const QString tgtLang = m_tgtLangEdit->text().trimmed();
    if (term.isEmpty() || translation.isEmpty()) return;

    m_glossary->add(term, translation, srcLang, tgtLang);
    m_termEdit->clear();
    m_translationEdit->clear();
    reload();
}

void GlossaryWidget::removeSelected() {
    const auto selected = m_table->selectionModel()->selectedRows();
    for (const auto& index : selected) {
        const QString term = m_table->item(index.row(), 0)->text();
        const QString langs = m_table->item(index.row(), 2)->text();
        const QStringList parts = langs.split(QStringLiteral("→"));
        if (parts.size() == 2) {
            m_glossary->remove(term, parts[0].trimmed(), parts[1].trimmed());
        }
    }
    reload();
}

void GlossaryWidget::importCsv() {
    const QString path = QFileDialog::getOpenFileName(this, tr("Импорт глоссария из CSV"), {},
                                                        tr("CSV-файлы (*.csv);;Все файлы (*)"));
    if (path.isEmpty()) return;
    const int count = m_glossary->loadCsv(path, m_srcLangEdit->text().trimmed(),
                                           m_tgtLangEdit->text().trimmed());
    QMessageBox::information(this, tr("Импорт глоссария"),
                              tr("Импортировано терминов: %1").arg(count));
    reload();
}

void GlossaryWidget::reload() {
    const auto entries = m_glossary->listAll();
    m_table->setRowCount(static_cast<int>(entries.size()));
    for (int i = 0; i < static_cast<int>(entries.size()); ++i) {
        const auto& entry = entries[static_cast<size_t>(i)];
        m_table->setItem(i, 0, new QTableWidgetItem(entry.term));
        m_table->setItem(i, 1, new QTableWidgetItem(entry.translation));
        m_table->setItem(i, 2,
                          new QTableWidgetItem(QStringLiteral("%1 → %2").arg(entry.srcLang, entry.tgtLang)));
        m_table->setItem(i, 3, new QTableWidgetItem());
    }
}

} // namespace pdftransl
