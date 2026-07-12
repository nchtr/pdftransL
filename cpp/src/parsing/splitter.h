#pragma once
// Разбор Markdown на типизированные структурные блоки. Сплиттер намеренно
// консервативен: всё, что он не смог классифицировать, остаётся Text
// (переводимым) — защиту формул берёт на себя слой маскировки, поэтому
// ошибка классификации никогда не портит математику. Порт
// pdftransl/parsing/splitter.py.
#include "core/models.h"
#include <vector>

namespace pdftransl {

// Типы блоков, которые считаются переводимым естественным текстом; формулы,
// код, картинки, HTML и библиография маскируются целиком (passthrough).
bool isTranslatableType(BlockType type);

// Разобрать Markdown-документ на упорядоченный список блоков.
std::vector<Block> splitMarkdown(const QString& markdown);

// Пометить блоки библиографии (после заголовка "References"/"Список
// литературы"/... и до следующего заголовка) типом References — они не
// переводятся. ЛЮБОЙ заголовок завершает секцию референсов (а
// заголовок-референс снова её открывает). outMarked, если не nullptr,
// получает число помеченных блоков.
std::vector<Block> markReferences(const std::vector<Block>& blocks, int* outMarked = nullptr);

// Превратить блоки в сегменты перевода: один блок — один сегмент;
// непереводимые типы (и пустые блоки) становятся passthrough-сегментами.
std::vector<Segment> toSegments(const std::vector<Block>& blocks);

// Собрать текстовые фрагменты обратно в единый Markdown-документ
// (непустые части, разделённые пустой строкой).
QString assemble(const std::vector<QString>& texts);
// Удобный оверлоад: собрать документ из финального текста сегментов
// (Segment::finalText()) в их исходном порядке.
QString assemble(const std::vector<Segment>& segments);

} // namespace pdftransl
