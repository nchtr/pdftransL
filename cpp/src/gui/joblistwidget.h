#pragma once
// Список задач: статус-бейджи и прогресс-бары. Хранит лёгкую модель задачи
// (JobInfo) для GUI — в отличие от core::JobResult (финальный результат),
// JobInfo описывает состояние выполняющейся/только что поставленной задачи
// и обновляется по мере прогресса (updateJob()) тем, что реально запускает
// перевод (менеджер задач/пайплайн — вне этого набора файлов).
#include <QMap>
#include <QString>
#include <QWidget>

class QListWidget;
class QListWidgetItem;

namespace pdftransl {

struct JobInfo {
    QString jobId;
    QString fileName;
    QString sourceLang;
    QString targetLang;
    QString status = QStringLiteral("queued"); // queued|running|completed|partial|failed|paused
    QString stage;                             // текущая стадия конвейера (для степпера)
    double progress = 0.0;                     // 0..1
    int etaSeconds = -1;                       // -1 = неизвестно
    QString error;
};

class JobListWidget : public QWidget {
    Q_OBJECT
public:
    explicit JobListWidget(QWidget* parent = nullptr);

    void addJob(const JobInfo& job);
    void updateJob(const JobInfo& job);
    JobInfo jobInfo(const QString& jobId) const;
    void removeJob(const QString& jobId);

signals:
    void jobSelected(const QString& jobId);

private:
    QListWidgetItem* findItem(const QString& jobId) const;
    void renderItem(QListWidgetItem* item, const JobInfo& job);

    QListWidget* m_list;
    QMap<QString, JobInfo> m_jobs;
};

} // namespace pdftransl
