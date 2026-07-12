#include "gui/uploadwidget.h"
#include "core/config.h"
#include "core/models.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QFileDialog>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QMimeData>

namespace pdftransl {

UploadWidget::UploadWidget(QWidget* parent) : QWidget(parent) {
    setAcceptDrops(true);
    auto* layout = new QVBoxLayout(this);

    m_dropLabel = new QLabel("Drop PDF here or click Browse", this);
    m_dropLabel->setAlignment(Qt::AlignCenter);
    m_dropLabel->setMinimumHeight(150);
    m_dropLabel->setStyleSheet("border: 2px dashed #666; border-radius: 8px; padding: 20px;");
    layout->addWidget(m_dropLabel);

    auto* fileRow = new QHBoxLayout;
    m_filePathEdit = new QLineEdit(this);
    m_filePathEdit->setReadOnly(true);
    m_filePathEdit->setPlaceholderText("No file selected");
    fileRow->addWidget(m_filePathEdit);
    auto* browseBtn = new QPushButton("Browse...", this);
    connect(browseBtn, &QPushButton::clicked, this, &UploadWidget::browse);
    fileRow->addWidget(browseBtn);
    layout->addLayout(fileRow);

    auto* langRow = new QHBoxLayout;
    langRow->addWidget(new QLabel("From:"));
    m_sourceLang = new QComboBox(this);
    m_sourceLang->addItems({"en", "de", "fr", "es", "zh", "ja", "ko", "ru"});
    langRow->addWidget(m_sourceLang);
    langRow->addWidget(new QLabel("To:"));
    m_targetLang = new QComboBox(this);
    m_targetLang->addItems({"ru", "en", "de", "fr", "es", "zh", "ja", "ko"});
    langRow->addWidget(m_targetLang);
    layout->addLayout(langRow);

    auto* provRow = new QHBoxLayout;
    provRow->addWidget(new QLabel("Provider:"));
    m_provider = new QComboBox(this);
    m_provider->addItems({"openrouter", "anthropic", "openai", "deepseek", "local"});
    provRow->addWidget(m_provider);
    provRow->addStretch();
    layout->addLayout(provRow);

    layout->addStretch();

    m_startBtn = new QPushButton("Start Translation", this);
    m_startBtn->setMinimumHeight(40);
    m_startBtn->setEnabled(false);
    connect(m_startBtn, &QPushButton::clicked, this, &UploadWidget::startTranslation);
    layout->addWidget(m_startBtn);
}

void UploadWidget::dragEnterEvent(QDragEnterEvent* event) {
    if (event->mimeData()->hasUrls()) {
        auto url = event->mimeData()->urls().first();
        if (url.toLocalFile().endsWith(".pdf", Qt::CaseInsensitive))
            event->acceptProposedAction();
    }
}

void UploadWidget::dropEvent(QDropEvent* event) {
    auto url = event->mimeData()->urls().first();
    m_selectedFile = url.toLocalFile();
    m_filePathEdit->setText(m_selectedFile);
    m_dropLabel->setText("File: " + QFileInfo(m_selectedFile).fileName());
    m_startBtn->setEnabled(true);
}

void UploadWidget::browse() {
    QString path = QFileDialog::getOpenFileName(this, "Select PDF", {}, "PDF Files (*.pdf)");
    if (path.isEmpty()) return;
    m_selectedFile = path;
    m_filePathEdit->setText(path);
    m_dropLabel->setText("File: " + QFileInfo(path).fileName());
    m_startBtn->setEnabled(true);
}

void UploadWidget::startTranslation() {
    if (m_selectedFile.isEmpty()) return;
    QString jobId = newId("job_");
    emit jobStarted(jobId);
}

} // namespace pdftransl
