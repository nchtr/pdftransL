#pragma once
// Серверные настройки «на лету»: провайдер, модель, парсер, воркеры, rpm,
// форматы, поведение пайплайна — сохраняются в SQLite и применяются как
// умолчания для новых задач. Порт idei
// frontend/src/components/SettingsPanel.jsx. Пустое поле/значение "—" тристейт-
// опции означает "использовать умолчание" (оверрайд не сохраняется).
#include <QMap>
#include <QString>
#include <QWidget>

class QComboBox;
class QLineEdit;
class QSpinBox;
class QPushButton;
class QLabel;
class QVBoxLayout;

namespace pdftransl {

class SettingsWidget : public QWidget {
    Q_OBJECT
public:
    explicit SettingsWidget(QWidget* parent = nullptr);

private slots:
    void save();

private:
    void load();
    QComboBox* addTriState(QVBoxLayout* layout, const QString& key, const QString& title);
    QString settingsDbPath() const;

    QComboBox* m_provider;
    QLineEdit* m_model;
    QLineEdit* m_visionModel;
    QComboBox* m_parser;
    QSpinBox* m_maxWorkers;
    QLineEdit* m_rpmLimit;
    QLineEdit* m_formats;
    QLineEdit* m_fallbackProviders;

    QMap<QString, QComboBox*> m_triStates; // ключ настройки -> тристейт-комбобокс
    QLabel* m_savedLabel;
    QPushButton* m_saveBtn;
};

} // namespace pdftransl
