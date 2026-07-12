#pragma once
#include <QWidget>
#include <QLabel>
#include <QComboBox>
#include <QPushButton>
#include <QLineEdit>

namespace pdftransl {

class UploadWidget : public QWidget {
    Q_OBJECT
public:
    explicit UploadWidget(QWidget* parent = nullptr);

signals:
    void jobStarted(const QString& jobId);

protected:
    void dragEnterEvent(QDragEnterEvent* event) override;
    void dropEvent(QDropEvent* event) override;

private slots:
    void browse();
    void startTranslation();

private:
    QLabel* m_dropLabel;
    QLineEdit* m_filePathEdit;
    QComboBox* m_sourceLang;
    QComboBox* m_targetLang;
    QComboBox* m_provider;
    QPushButton* m_startBtn;
    QString m_selectedFile;
};

} // namespace pdftransl
