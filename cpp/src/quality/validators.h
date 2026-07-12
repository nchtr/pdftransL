#pragma once
#include "core/models.h"
#include "core/config.h"
#include <vector>

namespace pdftransl {

std::vector<QAIssue> validateSegment(const Segment& seg, const PipelineConfig& config);

} // namespace pdftransl
