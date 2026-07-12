#pragma once
#include "core/models.h"
#include <QString>
#include <vector>

namespace pdftransl {

void exportHtml(const QString& markdown, const std::vector<Asset>& assets,
                const QString& outputPath);

} // namespace pdftransl
