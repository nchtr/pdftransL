#pragma once
#include "core/models.h"
#include <vector>

namespace pdftransl {

std::vector<Block> splitMarkdown(const QString& text);
QString assembleTranslation(const std::vector<Segment>& segments);

} // namespace pdftransl
