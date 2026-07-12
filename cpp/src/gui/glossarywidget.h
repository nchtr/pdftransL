#pragma once
// Глоссарий: таблица терминов, форма добавления/удаления, импорт CSV. Порт
// idei frontend/src/components/GlossaryPanel.jsx поверх rag::Glossary.
#include <QWidget>
#include <memory>

class QTableWidget;
class QLineEdit;
class QPushButton;
class QLabel;

namespace pdftransl {

class Glossary;

class GlossaryWidget : public QWidget {
    Q_OBJECT
public:
    explicit GlossaryWidget(QWidget* parent = nullptr);
    ~GlossaryWidget() override;

private slots:
    void addTerm();
    void removeSelected();
    void importCsv();

private:
    void reload();

    std::unique_ptr<Glossary> m_glossary;

    QTableWidget* m_table;
    QLineEdit* m_termEdit;
    QLineEdit* m_translationEdit;
    QLineEdit* m_srcLangEdit;
    QLineEdit* m_tgtLangEdit;
    QPushButton* m_addBtn;
    QPushButton* m_removeBtn;
    QPushButton* m_importBtn;
    QLabel* m_hintLabel;
};

} // namespace pdftransl
