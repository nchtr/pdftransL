#include "gui/joblistwidget.h"
#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QProgressBar>
#include <QVBoxLayout>

namespace pdftransl {

namespace {

QString statusLabel(const QString& status) {
    static const QMap<QString, QString> labels = {
        {"queued", QObject::tr("в очереди")},
        {"running", QObject::tr("выполняется")},
        {"completed", QObject::tr("готово")},
        {"partial", QObject::tr("готово (есть проблемы)")},
        {"failed", QObject::tr("ошибка")},
        {"paused", QObject::tr("на паузе")},
    };
    return labels.value(status, status);
}

QString statusBadgeStyle(const QString& status) {
    QString color = "#888";
    if (status == "completed") color = "#2e7d32";
    else if (status == "partial") color = "#f9a825";
    else if (status == "failed") color = "#c62828";
    else if (status == "paused") color = "#6d4c41";
    else if (status == "running") color = "#1565c0";
    return QStringLiteral(
               "background: %1; color: white; border-radius: 6px; padding: 1px 8px; "
               "font-size: 11px;")
        .arg(color);
}

} // namespace

JobListWidget::JobListWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);

    auto* title = new QLabel(tr("<b>Задачи перевода</b>"), this);
    layout->addWidget(title);

    m_list = new QListWidget(this);
    m_list->setSpacing(2);
    layout->addWidget(m_list, 1);

    connect(m_list, &QListWidget::itemClicked, this, [this](QListWidgetItem* item) {
        emit jobSelected(item->data(Qt::UserRole).toString());
    });
}

QListWidgetItem* JobListWidget::findItem(const QString& jobId) const {
    for (int i = 0; i < m_list->count(); ++i) {
        QListWidgetItem* item = m_list->item(i);
        if (item->data(Qt::UserRole).toString() == jobId) return item;
    }
    return nullptr;
}

void JobListWidget::renderItem(QListWidgetItem* item, const JobInfo& job) {
    auto* row = new QWidget();
    auto* rowLayout = new QVBoxLayout(row);
    rowLayout->setContentsMargins(6, 4, 6, 4);
    rowLayout->setSpacing(2);

    auto* titleRow = new QHBoxLayout();
    auto* nameLabel = new QLabel(job.fileName.isEmpty() ? job.jobId : job.fileName, row);
    nameLabel->setStyleSheet("font-weight: 600;");
    auto* badge = new QLabel(statusLabel(job.status), row);
    badge->setStyleSheet(statusBadgeStyle(job.status));
    titleRow->addWidget(nameLabel, 1);
    titleRow->addWidget(badge, 0);
    rowLayout->addLayout(titleRow);

    QString metaText = QStringLiteral("%1 → %2").arg(job.sourceLang, job.targetLang);
    if (job.status == QStringLiteral("running") && !job.stage.isEmpty()) {
        metaText += QStringLiteral(" · %1 (%2%)").arg(job.stage).arg(static_cast<int>(job.progress * 100));
    }
    auto* metaLabel = new QLabel(metaText, row);
    metaLabel->setStyleSheet("color: palette(mid); font-size: 11px;");
    rowLayout->addWidget(metaLabel);

    if (job.status == QStringLiteral("running") || job.status == QStringLiteral("queued")) {
        auto* bar = new QProgressBar(row);
        bar->setRange(0, 100);
        bar->setValue(static_cast<int>(job.progress * 100));
        bar->setTextVisible(false);
        bar->setFixedHeight(6);
        rowLayout->addWidget(bar);
    }

    item->setSizeHint(row->sizeHint());
    m_list->setItemWidget(item, row);
    item->setData(Qt::UserRole, job.jobId);
}

void JobListWidget::addJob(const JobInfo& job) {
    m_jobs.insert(job.jobId, job);
    auto* item = new QListWidgetItem();
    m_list->insertItem(0, item);
    renderItem(item, job);
}

void JobListWidget::updateJob(const JobInfo& job) {
    m_jobs.insert(job.jobId, job);
    QListWidgetItem* item = findItem(job.jobId);
    if (!item) {
        addJob(job);
        return;
    }
    renderItem(item, job);
}

JobInfo JobListWidget::jobInfo(const QString& jobId) const {
    return m_jobs.value(jobId);
}

void JobListWidget::removeJob(const QString& jobId) {
    m_jobs.remove(jobId);
    if (QListWidgetItem* item = findItem(jobId)) {
        delete m_list->takeItem(m_list->row(item));
    }
}

} // namespace pdftransl
