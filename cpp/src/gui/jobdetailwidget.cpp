#include "gui/jobdetailwidget.h"
#include <QDesktopServices>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QUrl>
#include <QVBoxLayout>
#include <QVector>
#include <utility>

namespace pdftransl {

namespace {

// Плановые стадии конвейера (ключ, русская подпись) — степпер их просто
// перечисляет; текущая стадия определяется по JobInfo::stage.
const QVector<std::pair<QString, QString>>& stagePlan() {
    static const QVector<std::pair<QString, QString>> plan = {
        {"parse", QObject::tr("Разбор PDF")},
        {"translate", QObject::tr("Перевод")},
        {"review", QObject::tr("Проверка качества")},
        {"export", QObject::tr("Экспорт")},
    };
    return plan;
}

const QMap<QString, QString>& formatTitles() {
    static const QMap<QString, QString> titles = {
        {"md", QStringLiteral("Markdown")},   {"html", QStringLiteral("HTML")},
        {"docx", QStringLiteral("DOCX")},     {"pdf", QStringLiteral("PDF")},
        {"latex", QStringLiteral("LaTeX (.tex)")},
    };
    return titles;
}

} // namespace

JobDetailWidget::JobDetailWidget(QWidget* parent) : QWidget(parent) {
    auto* layout = new QVBoxLayout(this);

    auto* header = new QHBoxLayout();
    m_titleLabel = new QLabel(tr("Задача не выбрана"), this);
    m_titleLabel->setStyleSheet("font-size: 15px; font-weight: 600;");
    header->addWidget(m_titleLabel, 1);
    m_statusLabel = new QLabel(this);
    m_statusLabel->setStyleSheet("font-weight: 600;");
    header->addWidget(m_statusLabel);
    layout->addLayout(header);

    m_errorLabel = new QLabel(this);
    m_errorLabel->setStyleSheet("color: #c62828;");
    m_errorLabel->setWordWrap(true);
    m_errorLabel->hide();
    layout->addWidget(m_errorLabel);

    m_stepperWidget = new QWidget(this);
    m_stepperLayout = new QHBoxLayout(m_stepperWidget);
    m_stepperLayout->setContentsMargins(0, 0, 0, 0);
    layout->addWidget(m_stepperWidget);

    auto* progressRow = new QHBoxLayout();
    m_progressBar = new QProgressBar(this);
    m_progressBar->setRange(0, 100);
    progressRow->addWidget(m_progressBar, 1);
    m_etaLabel = new QLabel(this);
    m_etaLabel->setStyleSheet("color: palette(mid);");
    progressRow->addWidget(m_etaLabel);
    layout->addLayout(progressRow);

    m_pauseResumeBtn = new QPushButton(tr("Пауза"), this);
    connect(m_pauseResumeBtn, &QPushButton::clicked, this, &JobDetailWidget::handlePauseResume);
    layout->addWidget(m_pauseResumeBtn);

    m_warningsLabel = new QLabel(this);
    m_warningsLabel->setWordWrap(true);
    m_warningsLabel->setStyleSheet("color: #f9a825;");
    m_warningsLabel->hide();
    layout->addWidget(m_warningsLabel);

    auto* downloadsTitle = new QLabel(tr("Скачать:"), this);
    layout->addWidget(downloadsTitle);
    m_downloadsWidget = new QWidget(this);
    m_downloadsLayout = new QHBoxLayout(m_downloadsWidget);
    m_downloadsLayout->setContentsMargins(0, 0, 0, 0);
    layout->addWidget(m_downloadsWidget);

    layout->addStretch();
    showJob(JobInfo{});
}

QString JobDetailWidget::statusText(const QString& status) {
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

QString JobDetailWidget::formatEta(int seconds) {
    if (seconds < 0) return {};
    if (seconds < 60) return tr("%1 с").arg(seconds);
    if (seconds < 3600) return tr("%1 мин").arg(seconds / 60);
    return tr("%1 ч %2 мин").arg(seconds / 3600).arg((seconds % 3600) / 60);
}

void JobDetailWidget::rebuildStageStepper() {
    QLayoutItem* child;
    while ((child = m_stepperLayout->takeAt(0)) != nullptr) {
        delete child->widget();
        delete child;
    }
    if (m_job.jobId.isEmpty()) return;

    int currentIndex = -1;
    const auto& plan = stagePlan();
    for (int i = 0; i < plan.size(); ++i) {
        if (plan[i].first == m_job.stage) { currentIndex = i; break; }
    }

    for (int i = 0; i < plan.size(); ++i) {
        QString marker;
        QString style = "padding: 2px 8px; border-radius: 4px;";
        if (currentIndex >= 0 && i < currentIndex) {
            marker = QStringLiteral("✓ "); // done
            style += "color: #2e7d32;";
        } else if (i == currentIndex) {
            style += m_job.status == QStringLiteral("failed") ? "color: #c62828; font-weight: 600;"
                     : m_job.status == QStringLiteral("paused") ? "color: #6d4c41; font-weight: 600;"
                                                                 : "color: #1565c0; font-weight: 600;";
        } else {
            style += "color: palette(mid);";
        }
        auto* label = new QLabel(marker + plan[i].second, m_stepperWidget);
        label->setStyleSheet(style);
        m_stepperLayout->addWidget(label);
        if (i + 1 < plan.size()) {
            auto* arrow = new QLabel(QStringLiteral("→"), m_stepperWidget);
            arrow->setStyleSheet("color: palette(mid);");
            m_stepperLayout->addWidget(arrow);
        }
    }
    m_stepperLayout->addStretch();
}

void JobDetailWidget::showJob(const JobInfo& job) {
    m_job = job;

    m_titleLabel->setText(job.jobId.isEmpty()
                               ? tr("Задача не выбрана")
                               : (job.fileName.isEmpty() ? job.jobId : job.fileName));
    m_statusLabel->setText(statusText(job.status));
    m_progressBar->setValue(static_cast<int>(job.progress * 100));

    const QString eta = formatEta(job.etaSeconds);
    m_etaLabel->setText(eta.isEmpty() ? QString() : tr("осталось: %1").arg(eta));

    if (!job.error.isEmpty()) {
        m_errorLabel->setText(job.error);
        m_errorLabel->show();
    } else {
        m_errorLabel->hide();
    }

    const bool active = job.status == QStringLiteral("running") || job.status == QStringLiteral("queued");
    m_pauseResumeBtn->setVisible(active || job.status == QStringLiteral("paused"));
    m_pauseResumeBtn->setText(job.status == QStringLiteral("paused") ? tr("Продолжить") : tr("Пауза"));
    m_pauseResumeBtn->setEnabled(job.status != QStringLiteral("queued"));

    rebuildStageStepper();
}

void JobDetailWidget::setWarnings(const QStringList& warnings) {
    if (warnings.isEmpty()) {
        m_warningsLabel->hide();
        return;
    }
    QStringList lines;
    for (const auto& w : warnings) lines << (QStringLiteral("⚠ ") + w);
    m_warningsLabel->setText(lines.join('\n'));
    m_warningsLabel->show();
}

void JobDetailWidget::setDownloads(const QMap<QString, QString>& formatToPath) {
    QLayoutItem* child;
    while ((child = m_downloadsLayout->takeAt(0)) != nullptr) {
        delete child->widget();
        delete child;
    }
    for (auto it = formatToPath.constBegin(); it != formatToPath.constEnd(); ++it) {
        const QString path = it.value();
        auto* btn = new QPushButton(formatTitles().value(it.key(), it.key()), m_downloadsWidget);
        connect(btn, &QPushButton::clicked, this,
                [path]() { QDesktopServices::openUrl(QUrl::fromLocalFile(path)); });
        m_downloadsLayout->addWidget(btn);
    }
    m_downloadsLayout->addStretch();
}

void JobDetailWidget::handlePauseResume() {
    if (m_job.jobId.isEmpty()) return;
    if (m_job.status == QStringLiteral("paused")) {
        emit resumeRequested(m_job.jobId);
    } else {
        emit pauseRequested(m_job.jobId);
    }
}

} // namespace pdftransl
