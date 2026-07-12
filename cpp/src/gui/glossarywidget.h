#pragma once
#include <QWidget>
#include <QTableWidget>
#include <QLineEdit>
#include <QPushButton>
#include "rag/glossary.h"
#include <memory>

namespace pdftransl {

class GlossaryWidget : public QWidget {
    Q_OBJECT
public:
    explicit GlossaryWidget(QWidget* parent = nullptr);

private slots:
    void addEntry();
    void removeSelected();
    void refresh();

private:
    QTableWidget* m_table;
    QLineEdit* m_termEdit;
    QLineEdit* m_translationEdit;
    QLineEdit* m_domainEdit;
    std::unique_ptr<Glossary> m_glossary;
};

} // namespace pdftransl
