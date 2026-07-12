#pragma once
// Карточка задачи: статус, прогресс, степпер стадий, ETA, пауза/продолжить,
// предупреждения из QA-отчёта, ссылки на скачивание готовых форматов.
#include "gui/joblistwidget.h"
#include <QMap>
#include <QStringList>
#include <QWidget>

class QLabel;
class QProgressBar;
class QPushButton;
class QVBoxLayout;
class QHBoxLayout;

namespace pdftransl {

class JobDetailWidget : public QWidget {
    Q_OBJECT
public:
    explicit JobDetailWidget(QWidget* parent = nullptr);

    // Отобразить состояние задачи (вызывается при выборе в JobListWidget и
    // при каждом обновлении прогресса).
    void showJob(const JobInfo& job);

    // Предупреждения из QA-отчёта (сканы, память, фолбэк парсера и т.п.).
    void setWarnings(const QStringList& warnings);

    // Готовые форматы для скачивания: формат ("html"/"docx"/"pdf"/...) -> путь к файлу.
    void setDownloads(const QMap<QString, QString>& formatToPath);

signals:
    void pauseRequested(const QString& jobId);
    void resumeRequested(const QString& jobId);

private slots:
    void handlePauseResume();

private:
    void rebuildStageStepper();
    static QString formatEta(int seconds);
    static QString statusText(const QString& status);

    JobInfo m_job;

    QLabel* m_titleLabel;
    QLabel* m_statusLabel;
    QLabel* m_etaLabel;
    QLabel* m_errorLabel;
    QProgressBar* m_progressBar;
    QWidget* m_stepperWidget;
    QHBoxLayout* m_stepperLayout;
    QPushButton* m_pauseResumeBtn;
    QLabel* m_warningsLabel;
    QWidget* m_downloadsWidget;
    QHBoxLayout* m_downloadsLayout;
};

} // namespace pdftransl
