#include "gui/glossarywidget.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QStandardPaths>

namespace pdftransl {

GlossaryWidget::GlossaryWidget(QWidget* parent) : QWidget(parent) {
    QString dbPath = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation)
                     + "/pdftransl.db";
    m_glossary = std::make_unique<Glossary>(dbPath);

    auto* layout = new QVBoxLayout(this);

    auto* addRow = new QHBoxLayout;
    m_termEdit = new QLineEdit(this);
    m_termEdit->setPlaceholderText("Term");
    addRow->addWidget(m_termEdit);
    m_translationEdit = new QLineEdit(this);
    m_translationEdit->setPlaceholderText("Translation");
    addRow->addWidget(m_translationEdit);
    m_domainEdit = new QLineEdit(this);
    m_domainEdit->setPlaceholderText("Domain (optional)");
    m_domainEdit->setMaximumWidth(150);
    addRow->addWidget(m_domainEdit);
    auto* addBtn = new QPushButton("Add", this);
    connect(addBtn, &QPushButton::clicked, this, &GlossaryWidget::addEntry);
    addRow->addWidget(addBtn);
    layout->addLayout(addRow);

    m_table = new QTableWidget(this);
    m_table->setColumnCount(3);
    m_table->setHorizontalHeaderLabels({"Term", "Translation", "Domain"});
    m_table->horizontalHeader()->setStretchLastSection(true);
    m_table->setSelectionBehavior(QAbstractItemView::SelectRows);
    layout->addWidget(m_table, 1);

    auto* removeBtn = new QPushButton("Remove Selected", this);
    connect(removeBtn, &QPushButton::clicked, this, &GlossaryWidget::removeSelected);
    layout->addWidget(removeBtn);

    refresh();
}

void GlossaryWidget::addEntry() {
    QString term = m_termEdit->text().trimmed();
    QString translation = m_translationEdit->text().trimmed();
    if (term.isEmpty() || translation.isEmpty()) return;

    m_glossary->add(term, translation, m_domainEdit->text().trimmed());
    m_termEdit->clear();
    m_translationEdit->clear();
    refresh();
}

void GlossaryWidget::removeSelected() {
    auto selected = m_table->selectionModel()->selectedRows();
    for (const auto& idx : selected) {
        QString term = m_table->item(idx.row(), 0)->text();
        QString domain = m_table->item(idx.row(), 2)->text();
        m_glossary->remove(term, domain);
    }
    refresh();
}

void GlossaryWidget::refresh() {
    auto entries = m_glossary->all();
    m_table->setRowCount(entries.size());
    for (int i = 0; i < entries.size(); ++i) {
        m_table->setItem(i, 0, new QTableWidgetItem(entries[i].term));
        m_table->setItem(i, 1, new QTableWidgetItem(entries[i].translation));
        m_table->setItem(i, 2, new QTableWidgetItem(entries[i].domain));
    }
}

} // namespace pdftransl
