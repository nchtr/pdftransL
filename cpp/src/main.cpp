#include "gui/mainwindow.h"
#include <QApplication>

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);
    app.setOrganizationName("pdftransl");
    app.setApplicationName("PDF Translator");
    app.setApplicationVersion("0.20.0");

    pdftransl::MainWindow window;
    window.show();

    return app.exec();
}
