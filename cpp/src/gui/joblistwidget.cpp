#include "gui/joblistwidget.h"
#include <QVBoxLayout>
#include <QLabel>

namespace pdftransl {

JobListWidget::JobListWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);
    layout->addWidget(new QLabel("<b>Translation Jobs</b>"));

    m_list = new QListWidget(this);
    layout->addWidget(m_list);

    connect(m_list, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem* item) {
        emit jobSelected(item->data(Qt::UserRole).toString());
    });
}

void JobListWidget::addJob(const QString& jobId, const QString& filename, const QString& status) {
    auto* item = new QListWidgetItem(QString("%1 — %2").arg(filename, status));
    item->setData(Qt::UserRole, jobId);
    m_list->insertItem(0, item);
}

} // namespace pdftransl
