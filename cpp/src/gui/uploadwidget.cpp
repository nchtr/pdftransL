#include "gui/uploadwidget.h"
#include <QCheckBox>
#include <QComboBox>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFileDialog>
#include <QFileInfo>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMimeData>
#include <QPushButton>
#include <QUrl>
#include <QVBoxLayout>

namespace pdftransl {

namespace {
const QStringList kLangs = {"en", "ru", "de", "fr", "es", "zh", "ja", "uk"};
} // namespace

UploadWidget::UploadWidget(QWidget* parent) : QWidget(parent) {
    setAcceptDrops(true);
    auto* layout = new QVBoxLayout(this);

    m_dropLabel = new QLabel(tr("Выберите PDF-файл или перетащите сюда"), this);
    m_dropLabel->setAlignment(Qt::AlignCenter);
    m_dropLabel->setMinimumHeight(120);
    m_dropLabel->setStyleSheet("border: 2px dashed palette(mid); border-radius: 8px; padding: 16px;");
    layout->addWidget(m_dropLabel);

    auto* browseRow = new QHBoxLayout();
    auto* browseBtn = new QPushButton(tr("Обзор…"), this);
    connect(browseBtn, &QPushButton::clicked, this, &UploadWidget::browse);
    browseRow->addStretch();
    browseRow->addWidget(browseBtn);
    layout->addLayout(browseRow);

    auto* langRow = new QHBoxLayout();
    langRow->addWidget(new QLabel(tr("Язык оригинала:"), this));
    m_sourceLang = new QComboBox(this);
    m_sourceLang->addItems(kLangs);
    langRow->addWidget(m_sourceLang);
    langRow->addWidget(new QLabel(tr("Язык перевода:"), this));
    m_targetLang = new QComboBox(this);
    m_targetLang->addItems(kLangs);
    m_targetLang->setCurrentText("ru");
    langRow->addWidget(m_targetLang);
    layout->addLayout(langRow);

    auto* providerRow = new QHBoxLayout();
    providerRow->addWidget(new QLabel(tr("Провайдер LLM:"), this));
    m_provider = new QComboBox(this);
    m_provider->addItem(tr("по умолчанию (сервер)"), QString());
    for (const QString& p :
         {"openrouter", "anthropic", "openai", "deepseek", "ollama", "vllm", "lmstudio"}) {
        m_provider->addItem(p, p);
    }
    providerRow->addWidget(m_provider);
    providerRow->addWidget(new QLabel(tr("Модель:"), this));
    m_model = new QLineEdit(this);
    m_model->setPlaceholderText(tr("например qwen2.5:14b"));
    providerRow->addWidget(m_model);
    layout->addLayout(providerRow);

    auto* parserRow = new QHBoxLayout();
    parserRow->addWidget(new QLabel(tr("Парсер PDF:"), this));
    m_parser = new QComboBox(this);
    m_parser->addItem(tr("по умолчанию (auto)"), QString());
    m_parser->addItem(tr("auto — лучший из установленных"), "auto");
    m_parser->addItem(tr("marker (быстрый)"), "marker");
    m_parser->addItem(tr("nougat (формулы, GPU)"), "nougat");
    m_parser->addItem(tr("docling (таблицы)"), "docling");
    parserRow->addWidget(m_parser);
    parserRow->addWidget(new QLabel(tr("OCR-модель:"), this));
    m_visionModel = new QLineEdit(this);
    m_visionModel->setPlaceholderText(tr("необязательно"));
    parserRow->addWidget(m_visionModel);
    layout->addLayout(parserRow);

    auto* formatsBox = new QGroupBox(tr("Форматы результата (markdown — всегда)"), this);
    auto* formatsLayout = new QHBoxLayout(formatsBox);
    m_fmtHtml = new QCheckBox("HTML", formatsBox);
    m_fmtHtml->setChecked(true);
    m_fmtDocx = new QCheckBox("DOCX", formatsBox);
    m_fmtDocx->setChecked(true);
    m_fmtPdf = new QCheckBox("PDF", formatsBox);
    m_fmtPdf->setChecked(true);
    m_fmtLatex = new QCheckBox(tr("LaTeX"), formatsBox);
    for (auto* box : {m_fmtHtml, m_fmtDocx, m_fmtPdf, m_fmtLatex}) formatsLayout->addWidget(box);
    layout->addWidget(formatsBox);

    auto* optionsBox = new QGroupBox(tr("Опции"), this);
    auto* optionsLayout = new QVBoxLayout(optionsBox);
    m_optReview = new QCheckBox(tr("LLM-ревью проблемных сегментов"), optionsBox);
    m_optReview->setChecked(true);
    m_optUseRag = new QCheckBox(tr("Память переводов / RAG"), optionsBox);
    m_optUseRag->setChecked(true);
    m_optBilingual = new QCheckBox(tr("Двуязычный документ (оригинал + перевод)"), optionsBox);
    m_optDescribeFigures = new QCheckBox(tr("VLM-описания рисунков"), optionsBox);
    m_optSkipReferences = new QCheckBox(tr("Не переводить список литературы"), optionsBox);
    m_optSkipReferences->setChecked(true);
    m_optQualityScore = new QCheckBox(tr("Оценка качества LLM-судьёй"), optionsBox);
    for (auto* box : {m_optReview, m_optUseRag, m_optBilingual, m_optDescribeFigures,
                       m_optSkipReferences, m_optQualityScore}) {
        optionsLayout->addWidget(box);
    }
    layout->addWidget(optionsBox);

    m_submitBtn = new QPushButton(tr("Перевести"), this);
    m_submitBtn->setMinimumHeight(36);
    m_submitBtn->setEnabled(false);
    connect(m_submitBtn, &QPushButton::clicked, this, &UploadWidget::submit);
    layout->addWidget(m_submitBtn);
}

void UploadWidget::dragEnterEvent(QDragEnterEvent* event) {
    if (event->mimeData()->hasUrls()) {
        const QString path = event->mimeData()->urls().first().toLocalFile();
        if (path.endsWith(".pdf", Qt::CaseInsensitive)) event->acceptProposedAction();
    }
}

void UploadWidget::dropEvent(QDropEvent* event) {
    if (!event->mimeData()->hasUrls()) return;
    setFile(event->mimeData()->urls().first().toLocalFile());
}

void UploadWidget::browse() {
    const QString path =
        QFileDialog::getOpenFileName(this, tr("Выберите PDF"), {}, tr("PDF-файлы (*.pdf)"));
    if (!path.isEmpty()) setFile(path);
}

void UploadWidget::setFile(const QString& path) {
    m_selectedFile = path;
    m_dropLabel->setText(tr("Файл: %1").arg(QFileInfo(path).fileName()));
    m_submitBtn->setEnabled(true);
}

PipelineConfig UploadWidget::buildConfig() const {
    PipelineConfig config = PipelineConfig::fromEnv();
    config.sourceLang = m_sourceLang->currentText();
    config.targetLang = m_targetLang->currentText();

    const QString provider = m_provider->currentData().toString();
    if (!provider.isEmpty()) config.provider = provider;
    if (!m_model->text().trimmed().isEmpty()) config.model = m_model->text().trimmed();

    const QString parser = m_parser->currentData().toString();
    if (!parser.isEmpty()) config.parserBackend = parser;
    if (!m_visionModel->text().trimmed().isEmpty()) config.visionModel = m_visionModel->text().trimmed();

    QStringList formats;
    if (m_fmtHtml->isChecked()) formats << "html";
    if (m_fmtDocx->isChecked()) formats << "docx";
    if (m_fmtPdf->isChecked()) formats << "pdf";
    if (m_fmtLatex->isChecked()) formats << "latex";
    config.exportFormats = formats;

    config.review = m_optReview->isChecked();
    config.useRag = m_optUseRag->isChecked();
    config.bilingual = m_optBilingual->isChecked();
    config.describeFigures = m_optDescribeFigures->isChecked();
    config.skipReferences = m_optSkipReferences->isChecked();
    config.qualityScore = m_optQualityScore->isChecked();
    return config;
}

void UploadWidget::submit() {
    if (m_selectedFile.isEmpty()) return;
    emit jobSubmitted(m_selectedFile, buildConfig());
}

} // namespace pdftransl
