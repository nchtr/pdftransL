#include "gui/mainwindow.h"
#include "core/config.h"
#include "core/models.h"
#include "gui/glossarywidget.h"
#include "gui/jobdetailwidget.h"
#include "gui/joblistwidget.h"
#include "gui/settingswidget.h"
#include "gui/uploadwidget.h"
#include "rag/store.h"
#include <QApplication>
#include <QFileInfo>
#include <QLabel>
#include <QPalette>
#include <QSplitter>
#include <QStatusBar>
#include <QTabWidget>
#include <QVBoxLayout>
#include <QWidget>

namespace pdftransl {

MainWindow::MainWindow(const PipelineConfig& config, QWidget* parent)
    : QMainWindow(parent), m_config(config) {
    applyDarkPalette();
    setupUi();

    try {
        m_translationMemory = std::make_unique<TranslationMemory>(m_config.dbPath);
    } catch (const std::exception&) {
        m_translationMemory.reset();
    }
    refreshTmStats();
}

MainWindow::~MainWindow() = default;

void MainWindow::setupUi() {
    setWindowTitle(tr("Переводчик PDF"));
    resize(1150, 780);

    m_tabs = new QTabWidget(this);
    setCentralWidget(m_tabs);

    // --- Вкладка "Перевод": форма загрузки + список задач + карточка задачи.
    m_upload = new UploadWidget(this);
    m_jobList = new JobListWidget(this);
    m_jobDetail = new JobDetailWidget(this);

    auto* leftSplitter = new QSplitter(Qt::Vertical);
    leftSplitter->addWidget(m_upload);
    leftSplitter->addWidget(m_jobList);
    leftSplitter->setStretchFactor(0, 0);
    leftSplitter->setStretchFactor(1, 1);

    auto* translateSplitter = new QSplitter(Qt::Horizontal);
    translateSplitter->addWidget(leftSplitter);
    translateSplitter->addWidget(m_jobDetail);
    translateSplitter->setStretchFactor(0, 1);
    translateSplitter->setStretchFactor(1, 1);

    m_tabs->addTab(translateSplitter, tr("Перевод"));

    // --- Вкладка "Настройки".
    m_settings = new SettingsWidget(this);
    m_tabs->addTab(m_settings, tr("Настройки"));

    // --- Вкладка "Глоссарий".
    m_glossary = new GlossaryWidget(this);
    m_tabs->addTab(m_glossary, tr("Глоссарий"));

    m_tmStatsLabel = new QLabel(this);
    statusBar()->addPermanentWidget(m_tmStatsLabel);

    connect(m_upload, &UploadWidget::jobSubmitted, this, &MainWindow::handleJobSubmitted);
    connect(m_jobList, &JobListWidget::jobSelected, this, &MainWindow::handleJobSelected);
    connect(m_jobDetail, &JobDetailWidget::pauseRequested, this, &MainWindow::handlePauseRequested);
    connect(m_jobDetail, &JobDetailWidget::resumeRequested, this, &MainWindow::handleResumeRequested);
}

void MainWindow::applyDarkPalette() {
    QPalette dark;
    dark.setColor(QPalette::Window, QColor(30, 30, 30));
    dark.setColor(QPalette::WindowText, QColor(220, 220, 220));
    dark.setColor(QPalette::Base, QColor(40, 40, 40));
    dark.setColor(QPalette::AlternateBase, QColor(50, 50, 50));
    dark.setColor(QPalette::Text, QColor(220, 220, 220));
    dark.setColor(QPalette::Button, QColor(50, 50, 50));
    dark.setColor(QPalette::ButtonText, QColor(220, 220, 220));
    dark.setColor(QPalette::Highlight, QColor(70, 130, 200));
    dark.setColor(QPalette::HighlightedText, Qt::white);
    dark.setColor(QPalette::Link, QColor(100, 160, 230));
    dark.setColor(QPalette::PlaceholderText, QColor(140, 140, 140));
    qApp->setPalette(dark);
}

void MainWindow::refreshTmStats() {
    if (!m_translationMemory) {
        m_tmStatsLabel->setText(tr("Память переводов недоступна"));
        return;
    }
    const auto stats = m_translationMemory->stats();
    m_tmStatsLabel->setText(
        tr("Память переводов: %1 сегм., %2 ручных правок").arg(stats.segments).arg(stats.humanCorrections));
}

void MainWindow::handleJobSubmitted(const QString& pdfPath, const PipelineConfig& config) {
    // Постановка задачи в очередь: реальное выполнение (парсинг, перевод,
    // экспорт) обеспечивает менеджер задач/пайплайн — здесь только GUI-
    // состояние очереди, обновляемое по мере прогресса через
    // JobListWidget::updateJob()/JobDetailWidget::showJob().
    JobInfo job;
    job.jobId = newId("job_");
    job.fileName = QFileInfo(pdfPath).fileName();
    job.sourceLang = config.sourceLang;
    job.targetLang = config.targetLang;
    job.status = QStringLiteral("queued");

    m_jobList->addJob(job);
    m_jobDetail->showJob(job);
}

void MainWindow::handleJobSelected(const QString& jobId) {
    m_jobDetail->showJob(m_jobList->jobInfo(jobId));
}

void MainWindow::handlePauseRequested(const QString& jobId) {
    JobInfo job = m_jobList->jobInfo(jobId);
    job.status = QStringLiteral("paused");
    m_jobList->updateJob(job);
    m_jobDetail->showJob(job);
}

void MainWindow::handleResumeRequested(const QString& jobId) {
    JobInfo job = m_jobList->jobInfo(jobId);
    job.status = QStringLiteral("running");
    m_jobList->updateJob(job);
    m_jobDetail->showJob(job);
}

} // namespace pdftransl
