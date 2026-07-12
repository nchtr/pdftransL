#pragma once
// Markdown -> автономный HTML с KaTeX-формулами (CDN). Свой конвертер поверх
// parsing::splitMarkdown — понимает ровно тот markdown, который производит
// пайплайн. Картинки инлайнятся data-URI, если найдены под assetsDir.
// Порт pdftransl/export/html.py.
#include <QString>

namespace pdftransl {

// Возвращает false, если файл не удалось записать (родительский каталог
// создаётся автоматически).
bool exportHtml(const QString& markdown, const QString& outPath, const QString& assetsDir = {},
                 const QString& title = QStringLiteral("Translated document"));

} // namespace pdftransl
