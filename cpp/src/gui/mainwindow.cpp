#include "gui/mainwindow.h"
#include "gui/uploadwidget.h"
#include "gui/joblistwidget.h"
#include "gui/jobdetailwidget.h"
#include "gui/settingswidget.h"
#include "gui/glossarywidget.h"
#include <QApplication>
#include <QPalette>
#include <QStyleHints>

namespace pdftransl {

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    setupUi();
    applyDarkPalette();
    showUpload();
}

void MainWindow::setupUi() {
    setWindowTitle("PDF Translator");
    resize(1024, 700);

    m_toolbar = addToolBar("Navigation");
    m_toolbar->setMovable(false);
    m_toolbar->addAction("Upload", this, &MainWindow::showUpload);
    m_toolbar->addAction("Jobs", this, &MainWindow::showJobs);
    m_toolbar->addAction("Settings", this, &MainWindow::showSettings);
    m_toolbar->addAction("Glossary", this, &MainWindow::showGlossary);

    m_stack = new QStackedWidget(this);
    setCentralWidget(m_stack);

    m_upload = new UploadWidget(this);
    m_jobList = new JobListWidget(this);
    m_jobDetail = new JobDetailWidget(this);
    m_settings = new SettingsWidget(this);
    m_glossary = new GlossaryWidget(this);

    m_stack->addWidget(m_upload);
    m_stack->addWidget(m_jobList);
    m_stack->addWidget(m_jobDetail);
    m_stack->addWidget(m_settings);
    m_stack->addWidget(m_glossary);

    connect(m_jobList, &JobListWidget::jobSelected, this, &MainWindow::openJob);
    connect(m_upload, &UploadWidget::jobStarted, this, [this](const QString& id) {
        openJob(id);
    });
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
    qApp->setPalette(dark);
}

void MainWindow::showUpload() { m_stack->setCurrentWidget(m_upload); }
void MainWindow::showJobs() { m_stack->setCurrentWidget(m_jobList); }
void MainWindow::showSettings() { m_stack->setCurrentWidget(m_settings); }
void MainWindow::showGlossary() { m_stack->setCurrentWidget(m_glossary); }

void MainWindow::openJob(const QString& jobId) {
    m_jobDetail->loadJob(jobId);
    m_stack->setCurrentWidget(m_jobDetail);
}

} // namespace pdftransl
