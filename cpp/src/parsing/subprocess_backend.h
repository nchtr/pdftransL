#pragma once
#include "core/models.h"
#include "core/config.h"

namespace pdftransl {

ParsedDocument parseWithSubprocess(const QString& pdfPath, const PipelineConfig& config);

} // namespace pdftransl
