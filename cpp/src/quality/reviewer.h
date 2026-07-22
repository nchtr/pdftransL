#pragma once
// LLM-ревью — вторая линия самоконтроля (после детерминированных
// validators). Ревьюер перепроверяет уже помеченные (флагованные)
// валидатором или циклом починки сегменты: JSON-вердикт {"ok": true} либо
// {"ok": false, "revised": ...}. Ревизия принимается только если не теряет
// содержимое плейсхолдеров; иначе остаётся исходный перевод с пометкой.
// Сбой ревью не фатален — сегмент возвращается как есть, с предупреждением.
// Порт pdftransl/quality/reviewer.py.
#include "core/models.h"
#include "llm/base.h"
#include <vector>

namespace pdftransl {

class Reviewer {
public:
    explicit Reviewer(LLMClientPtr client);

    // flagged — сегменты с непустыми issues (warning/error) после перевода;
    // возвращает тот же список с translation/issues, обновлёнными там, где
    // ревьюер подтвердил или обоснованно исправил перевод. Сбой ревью на
    // одном сегменте не прерывает обработку остальных.
    std::vector<Segment> reviewSegments(const std::vector<Segment>& flagged,
                                         const QString& sourceLang,
                                         const QString& targetLang);

private:
    Segment reviewOne(Segment segment, const QString& sourceLang, const QString& targetLang);

    LLMClientPtr m_client;
};

} // namespace pdftransl
