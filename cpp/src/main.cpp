#include "core/config.h"
#include "gui/mainwindow.h"
#include <QApplication>

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);
    app.setOrganizationName("pdftransl");
    app.setApplicationName("PDF Translator");
    app.setApplicationVersion("0.20.0");

    // Прогреваем конфигурацию из окружения (PDFTRANSL_*) один раз при
    // старте — MainWindow и виджеты настроек читают её же независимо при
    // обращении к общей базе (память переводов/глоссарий/настройки), но
    // ранняя загрузка здесь позволяет обнаружить проблемы с env до показа
    // окна.
    const pdftransl::PipelineConfig config = pdftransl::PipelineConfig::fromEnv();

    pdftransl::MainWindow window(config);
    window.show();

    return app.exec();
}
