#pragma once
#include <QWidget>
#include <QComboBox>
#include <QSpinBox>
#include <QCheckBox>
#include <QLineEdit>

namespace pdftransl {

class SettingsWidget : public QWidget {
    Q_OBJECT
public:
    explicit SettingsWidget(QWidget* parent = nullptr);

private slots:
    void saveSettings();

private:
    QComboBox* m_provider;
    QLineEdit* m_model;
    QLineEdit* m_apiKey;
    QSpinBox* m_maxWorkers;
    QSpinBox* m_batchSize;
    QCheckBox* m_useRag;
    QCheckBox* m_review;
    QComboBox* m_parser;
    QSpinBox* m_maxRetries;
};

} // namespace pdftransl
