#include "gui/jobdetailwidget.h"
#include <QVBoxLayout>
#include <QHBoxLayout>

namespace pdftransl {

JobDetailWidget::JobDetailWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);

    auto* header = new QHBoxLayout;
    m_jobIdLabel = new QLabel(this);
    header->addWidget(m_jobIdLabel);
    header->addStretch();
    m_statusLabel = new QLabel("Idle", this);
    m_statusLabel->setStyleSheet("font-weight: bold;");
    header->addWidget(m_statusLabel);
    layout->addLayout(header);

    m_progressBar = new QProgressBar(this);
    m_progressBar->setRange(0, 100);
    m_progressBar->setValue(0);
    layout->addWidget(m_progressBar);

    m_pauseBtn = new QPushButton("Pause", this);
    connect(m_pauseBtn, &QPushButton::clicked, this, &JobDetailWidget::togglePause);
    layout->addWidget(m_pauseBtn);

    m_preview = new QTextBrowser(this);
    m_preview->setOpenExternalLinks(true);
    layout->addWidget(m_preview, 1);
}

void JobDetailWidget::loadJob(const QString& jobId) {
    m_jobId = jobId;
    m_jobIdLabel->setText("Job: " + jobId);
    m_statusLabel->setText("Running");
    m_progressBar->setValue(0);
    m_paused = false;
    m_pauseBtn->setText("Pause");
    m_preview->clear();
}

void JobDetailWidget::setProgress(int done, int total) {
    if (total > 0)
        m_progressBar->setValue(done * 100 / total);
}

void JobDetailWidget::setStatus(const QString& status) {
    m_statusLabel->setText(status);
    if (status == "completed" || status == "failed")
        m_pauseBtn->setEnabled(false);
}

void JobDetailWidget::setPreview(const QString& markdown) {
    m_preview->setMarkdown(markdown);
}

void JobDetailWidget::togglePause() {
    m_paused = !m_paused;
    m_pauseBtn->setText(m_paused ? "Resume" : "Pause");
    m_statusLabel->setText(m_paused ? "Paused" : "Running");
}

} // namespace pdftransl
