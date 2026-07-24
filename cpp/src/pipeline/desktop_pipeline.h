#pragma once

#include "core/config.h"
#include "core/models.h"
#include "translation/translator.h"
#include <functional>

namespace pdftransl {

using StageProgressFn = std::function<void(const QString& stage, double progress)>;

// The desktop application's executable pipeline.  It intentionally uses the
// same parser, translator, checkpoint and exporter components as the rest of
// the C++ implementation instead of merely displaying a queued job.
class DesktopPipeline {
public:
    explicit DesktopPipeline(PipelineConfig config);

    JobResult run(const QString& pdfPath, const QString& jobId,
                  StageProgressFn onStage = nullptr,
                  ShouldPauseFn shouldPause = nullptr) const;

private:
    PipelineConfig m_config;
};

} // namespace pdftransl
