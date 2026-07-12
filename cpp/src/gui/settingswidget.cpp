#include "gui/settingswidget.h"
#include <QVBoxLayout>
#include <QFormLayout>
#include <QPushButton>
#include <QSettings>
#include <QMessageBox>

namespace pdftransl {

SettingsWidget::SettingsWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    auto* form = new QFormLayout;

    m_provider = new QComboBox(this);
    m_provider->addItems({"openrouter", "anthropic", "openai", "deepseek", "local"});
    form->addRow("Provider:", m_provider);

    m_model = new QLineEdit(this);
    m_model->setPlaceholderText("e.g. anthropic/claude-sonnet-4");
    form->addRow("Model:", m_model);

    m_apiKey = new QLineEdit(this);
    m_apiKey->setEchoMode(QLineEdit::Password);
    m_apiKey->setPlaceholderText("API key (stored locally)");
    form->addRow("API Key:", m_apiKey);

    m_maxWorkers = new QSpinBox(this);
    m_maxWorkers->setRange(1, 16);
    m_maxWorkers->setValue(4);
    form->addRow("Max Workers:", m_maxWorkers);

    m_batchSize = new QSpinBox(this);
    m_batchSize->setRange(5, 200);
    m_batchSize->setValue(40);
    form->addRow("Batch Size:", m_batchSize);

    m_maxRetries = new QSpinBox(this);
    m_maxRetries->setRange(0, 10);
    m_maxRetries->setValue(3);
    form->addRow("Max Retries:", m_maxRetries);

    m_parser = new QComboBox(this);
    m_parser->addItems({"auto", "marker", "nougat", "pymupdf", "docling"});
    form->addRow("Parser:", m_parser);

    m_useRag = new QCheckBox("Enable Translation Memory", this);
    m_useRag->setChecked(true);
    form->addRow(m_useRag);

    m_review = new QCheckBox("Enable QA Review", this);
    m_review->setChecked(true);
    form->addRow(m_review);

    layout->addLayout(form);
    layout->addStretch();

    auto* saveBtn = new QPushButton("Save Settings", this);
    connect(saveBtn, &QPushButton::clicked, this, &SettingsWidget::saveSettings);
    layout->addWidget(saveBtn);

    // Load saved settings
    QSettings s;
    m_provider->setCurrentText(s.value("provider", "openrouter").toString());
    m_model->setText(s.value("model").toString());
    m_maxWorkers->setValue(s.value("maxWorkers", 4).toInt());
    m_batchSize->setValue(s.value("batchSize", 40).toInt());
    m_parser->setCurrentText(s.value("parser", "auto").toString());
    m_useRag->setChecked(s.value("useRag", true).toBool());
    m_review->setChecked(s.value("review", true).toBool());
}

void SettingsWidget::saveSettings() {
    QSettings s;
    s.setValue("provider", m_provider->currentText());
    s.setValue("model", m_model->text());
    if (!m_apiKey->text().isEmpty())
        s.setValue("apiKey", m_apiKey->text());
    s.setValue("maxWorkers", m_maxWorkers->value());
    s.setValue("batchSize", m_batchSize->value());
    s.setValue("maxRetries", m_maxRetries->value());
    s.setValue("parser", m_parser->currentText());
    s.setValue("useRag", m_useRag->isChecked());
    s.setValue("review", m_review->isChecked());
    QMessageBox::information(this, "Settings", "Settings saved.");
}

} // namespace pdftransl
