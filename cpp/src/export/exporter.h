#pragma once
#include "core/models.h"
#include "core/config.h"
#include <QString>
#include <QVariantMap>

namespace pdftransl {

QVariantMap exportAll(const QString& markdown, const std::vector<Asset>& assets,
                     const PipelineConfig& config, const QString& outputDir);

} // namespace pdftransl
