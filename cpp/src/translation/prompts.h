#pragma once
// Все промпты: перевод, исправление, ревью. Порт pdftransl/translation/prompts.py.
//
// Системный переводческий промпт несёт правила плейсхолдеров и markdown-
// структуры; сюда же подмешиваются глоссарий и саммари документа.
#include <QString>
#include <QStringList>

namespace pdftransl {

// Человекочитаемое имя языка по коду ("ru" -> "Russian"); при отсутствии в
// таблице возвращает сам код без изменений.
QString langName(const QString& code);

// glossaryHints — готовые строки вида "term -> translation" (см.
// Glossary::match / документный глоссарий); context — краткое саммари
// документа, подмешивается как ограничивающий контекст.
QString buildTranslationSystem(const QString& sourceLang, const QString& targetLang,
                                const QString& context = {},
                                const QStringList& glossaryHints = {});

// previousContext — хвост предыдущего сегмента с исходной стороны (сглаживает
// швы между соседними сегментами при переводе); необязателен.
QString buildUserMessage(const QString& maskedText, const QString& previousContext = {});

// Шаблон повторного запроса при найденных валидатором проблемах.
// Плейсхолдеры: %1 = список проблем, %2 = исходный (масштабированный) текст,
// %3 = предыдущий (неудачный) перевод.
extern const QString REPAIR_USER;

// Промпты LLM-ревью (второй линии самоконтроля, quality::Reviewer).
// %1 = исходный язык, %2 = целевой язык.
extern const QString REVIEW_SYSTEM;
// %1 = исходный (масштабированный) текст, %2 = кандидат перевода.
extern const QString REVIEW_USER;

} // namespace pdftransl
