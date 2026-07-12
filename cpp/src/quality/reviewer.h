#pragma once
#include "core/models.h"
#include "core/config.h"
#include "llm/base.h"
#include <vector>

namespace pdftransl {

void reviewSegments(std::vector<Segment>& segments, const PipelineConfig& config,
                    LLMClientPtr client = nullptr);

} // namespace pdftransl
