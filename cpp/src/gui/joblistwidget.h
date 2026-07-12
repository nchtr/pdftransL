#pragma once
#include <QWidget>
#include <QListWidget>

namespace pdftransl {

class JobListWidget : public QWidget {
    Q_OBJECT
public:
    explicit JobListWidget(QWidget* parent = nullptr);
    void addJob(const QString& jobId, const QString& filename, const QString& status);

signals:
    void jobSelected(const QString& jobId);

private:
    QListWidget* m_list;
};

} // namespace pdftransl
