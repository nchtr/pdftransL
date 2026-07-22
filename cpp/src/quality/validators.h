#pragma once
// Детерминированные (без LLM) проверки перевода — первая линия
// самоконтроля. Дёшево и быстро ловим типичные провалы LLM: потерянные
// плейсхолдеры, разгон/усадку длины, непереведённые куски, сломанные
// таблицы/заголовки. Ошибки (level == "error") запускают цикл исправлений в
// Translator; предупреждения идут в QA-отчёт, не блокируя сегмент. Порт
// pdftransl/quality/validators.py.
#include "core/config.h"
#include "core/models.h"
#include <vector>

namespace pdftransl {

// Доля слов текста, оставшихся в письменности исходного языка (0.0, если
// системы письма исходного/целевого языков неразличимы — например
// латиница -> латиница). Используется и здесь, и стадией доперевода в
// пайплайне.
double residualSourceRatio(const QString& text, const QString& sourceLang,
                            const QString& targetLang);

// Провалидировать переведённый сегмент относительно его источника.
// Целостность плейсхолдеров проверяется отдельно при восстановлении
// (Translator::finalizeAttempt), здесь — независимая подстраховка:
// остаточные "⟦PHn⟧"-подобные токены в тексте перевода тоже считаются
// ошибкой.
std::vector<QAIssue> validateSegment(const Segment& segment, const PipelineConfig& config);

} // namespace pdftransl
