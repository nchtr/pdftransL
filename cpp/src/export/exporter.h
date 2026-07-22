#pragma once
// Оркестрация экспорта с мягким фолбэком движков: html/latex — встроенные
// конвертеры (всегда доступны); docx/pdf — через pandoc, если установлен.
// Порт (упрощённый) pdftransl/export/exporter.py: без chromium/xelatex/
// weasyprint цепочки для PDF — только pandoc, чтобы не тянуть в C++-сборку
// headless-браузер; остальные движки может добавить пайплайн при
// необходимости, дописав в exporter.cpp.
#include <QString>
#include <QStringList>
#include <QVariantMap>

namespace pdftransl {

// outBase — путь без расширения ("... /article.ru" -> article.ru.html /
// .tex / .docx / .pdf). Возвращает
// {"files": {fmt: path|QVariant()}, "engines": {fmt: "builtin"|"pandoc"|
// "unavailable: <причина>"}}.
QVariantMap exportDocument(const QString& markdown, const QString& outBase,
                            const QStringList& formats, const QString& assetsDir = {},
                            const QString& title = QStringLiteral("Translated document"));

} // namespace pdftransl
