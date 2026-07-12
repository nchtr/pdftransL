#pragma once
// Форма новой задачи: файл (drag-and-drop или выбор), языки, провайдер и
// модель перевода, парсер PDF, форматы экспорта, опции пайплайна. Порт
// idei frontend/src/components/UploadForm.jsx.
#include "core/config.h"
#include <QWidget>

class QLabel;
class QComboBox;
class QLineEdit;
class QPushButton;
class QCheckBox;
class QDragEnterEvent;
class QDropEvent;

namespace pdftransl {

class UploadWidget : public QWidget {
    Q_OBJECT
public:
    explicit UploadWidget(QWidget* parent = nullptr);

signals:
    // pdfPath — выбранный файл; config — параметры перевода, собранные из
    // формы (языки, провайдер/модель, парсер, форматы, опции).
    void jobSubmitted(const QString& pdfPath, const PipelineConfig& config);

protected:
    void dragEnterEvent(QDragEnterEvent* event) override;
    void dropEvent(QDropEvent* event) override;

private slots:
    void browse();
    void submit();

private:
    void setFile(const QString& path);
    PipelineConfig buildConfig() const;

    QString m_selectedFile;

    QLabel* m_dropLabel;
    QComboBox* m_sourceLang;
    QComboBox* m_targetLang;
    QComboBox* m_provider;
    QLineEdit* m_model;
    QComboBox* m_parser;
    QLineEdit* m_visionModel;

    QCheckBox* m_fmtHtml;
    QCheckBox* m_fmtDocx;
    QCheckBox* m_fmtPdf;
    QCheckBox* m_fmtLatex;

    QCheckBox* m_optReview;
    QCheckBox* m_optUseRag;
    QCheckBox* m_optBilingual;
    QCheckBox* m_optDescribeFigures;
    QCheckBox* m_optSkipReferences;
    QCheckBox* m_optQualityScore;

    QPushButton* m_submitBtn;
};

} // namespace pdftransl
