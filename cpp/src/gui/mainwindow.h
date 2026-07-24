#pragma once
// Главное окно: QTabWidget с тремя вкладками (Перевод, Настройки,
// Глоссарий) и статус-бар со статистикой памяти переводов. Тёмная тема
// применяется через QPalette.
#include "core/config.h"
#include <QMainWindow>
#include <QHash>
#include <QSharedPointer>
#include <atomic>
#include <memory>

class QTabWidget;
class QLabel;

namespace pdftransl {

class UploadWidget;
class JobListWidget;
class JobDetailWidget;
class SettingsWidget;
class GlossaryWidget;
class TranslationMemory;
template <typename T> class QFutureWatcher;
struct JobResult;

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(const PipelineConfig& config = PipelineConfig::fromEnv(),
                         QWidget* parent = nullptr);
    ~MainWindow() override;

private slots:
    void handleJobSubmitted(const QString& pdfPath, const PipelineConfig& config);
    void handleJobSelected(const QString& jobId);
    void handlePauseRequested(const QString& jobId);
    void handleResumeRequested(const QString& jobId);

private:
    void setupUi();
    void applyDarkPalette();
    void refreshTmStats();
    void startJob(const QString& jobId);
    void updateJobProgress(const QString& jobId, const QString& stage, double progress);

    PipelineConfig m_config;

    QTabWidget* m_tabs;
    UploadWidget* m_upload;
    JobListWidget* m_jobList;
    JobDetailWidget* m_jobDetail;
    SettingsWidget* m_settings;
    GlossaryWidget* m_glossary;

    std::unique_ptr<TranslationMemory> m_translationMemory;
    QLabel* m_tmStatsLabel;

    struct QueuedJob {
        QString pdfPath;
        PipelineConfig config;
        QSharedPointer<std::atomic_bool> pauseRequested;
    };
    QHash<QString, QueuedJob> m_queuedJobs;
    QHash<QString, QFutureWatcher<JobResult>*> m_runningJobs;
};

} // namespace pdftransl
