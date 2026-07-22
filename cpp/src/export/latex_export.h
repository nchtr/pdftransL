#pragma once
// Markdown -> компилируемый LaTeX-документ (xelatex/tectonic: шрифт
// DejaVu Serif покрывает кириллицу из коробки). Заголовки -> секции,
// математика проходит почти без изменений, таблицы -> tabular, картинки ->
// figure. Порт pdftransl/export/latex.py.
#include <QString>

namespace pdftransl {

// Возвращает false, если файл не удалось записать (родительский каталог
// создаётся автоматически).
bool exportLatex(const QString& markdown, const QString& outPath,
                  const QString& title = {});

} // namespace pdftransl
