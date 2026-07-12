#pragma once
#include <QWidget>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QTextBrowser>

namespace pdftransl {

class JobDetailWidget : public QWidget {
    Q_OBJECT
public:
    explicit JobDetailWidget(QWidget* parent = nullptr);
    void loadJob(const QString& jobId);
    void setProgress(int done, int total);
    void setStatus(const QString& status);
    void setPreview(const QString& markdown);

private slots:
    void togglePause();

private:
    QString m_jobId;
    bool m_paused = false;

    QLabel* m_statusLabel;
    QLabel* m_jobIdLabel;
    QProgressBar* m_progressBar;
    QPushButton* m_pauseBtn;
    QTextBrowser* m_preview;
};

} // namespace pdftransl
