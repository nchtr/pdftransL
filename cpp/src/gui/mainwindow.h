#pragma once
#include <QMainWindow>
#include <QStackedWidget>
#include <QToolBar>

namespace pdftransl {

class UploadWidget;
class JobListWidget;
class JobDetailWidget;
class SettingsWidget;
class GlossaryWidget;

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);

private slots:
    void showUpload();
    void showJobs();
    void showSettings();
    void showGlossary();
    void openJob(const QString& jobId);

private:
    void setupUi();
    void applyDarkPalette();

    QStackedWidget* m_stack;
    QToolBar* m_toolbar;
    UploadWidget* m_upload;
    JobListWidget* m_jobList;
    JobDetailWidget* m_jobDetail;
    SettingsWidget* m_settings;
    GlossaryWidget* m_glossary;
};

} // namespace pdftransl
