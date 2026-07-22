#pragma once
// Запуск внешних PDF-парсеров (marker, nougat, docling, ...) через QProcess.
// Порт идеи pdftransl/parsing/base.py + marker_backend.py/nougat_backend.py,
// обобщённой в один настраиваемый класс, так как в C++ нет удобного способа
// импортировать питоновские библиотеки этих инструментов напрямую — здесь
// мы полагаемся на их CLI.
#include "core/models.h"
#include <QString>
#include <QStringList>

namespace pdftransl {

// Контракт парсер-бэкенда: превращает PDF в Markdown (с LaTeX-формулами) +
// экспортированные изображения. SubprocessBackend реализует его поверх
// внешнего CLI-инструмента; будущие нативные бэкенды (например, MuPDF)
// наследуют тот же интерфейс.
class ParserBackend {
public:
    virtual ~ParserBackend() = default;
    virtual QString name() const = 0;
    virtual bool available() const = 0;
    virtual ParsedDocument parse(const QString& pdfPath, const QString& workdir) = 0;
};

// Описание того, как вызывать один внешний парсер: шаблон аргументов
// командной строки с плейсхолдерами {input}/{output}, и список шаблонов
// имён файлов (в порядке предпочтения), где искать получившийся Markdown
// внутри workdir после завершения процесса.
struct SubprocessBackendSpec {
    QString name;              // "marker" | "nougat" | "docling" | ...
    QString executable;        // имя/путь исполняемого файла (ищется через PATH)
    QStringList argsTemplate;  // аргументы; токены {input}/{output} подставляются
    QStringList markdownGlobs; // шаблоны имён файлов, например "*.md", "*.mmd"
    int timeoutSeconds = 1800;
};

// Готовые описания инструментов из задачи. Точные флаги командной строки
// специфичны для версии инструмента — при необходимости
// SubprocessBackendSpec можно собрать вручную под конкретный релиз.
SubprocessBackendSpec markerBackendSpec();
SubprocessBackendSpec nougatBackendSpec();
SubprocessBackendSpec doclingBackendSpec();

// Бэкенд-обёртка над внешним CLI-парсером PDF: запускает процесс, ждёт
// timeoutSeconds, ищет результирующий Markdown по markdownGlobs и собирает
// рядом лежащие изображения как ассеты (Asset). Бросает std::runtime_error
// при отсутствии исполняемого файла, таймауте, ненулевом коде возврата или
// отсутствии markdown на выходе.
class SubprocessBackend : public ParserBackend {
public:
    explicit SubprocessBackend(SubprocessBackendSpec spec);

    QString name() const override;
    bool available() const override;
    ParsedDocument parse(const QString& pdfPath, const QString& workdir) override;

private:
    SubprocessBackendSpec m_spec;
};

} // namespace pdftransl
