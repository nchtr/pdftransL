#pragma once
#include "core/config.h"
#include "core/models.h"
#include "llm/base.h"
#include "translation/masker.h"
#include "translation/checkpoint.h"
#include <functional>
#include <memory>

namespace pdftransl {

using ShouldPauseFn = std::function<bool()>;
using ProgressFn = std::function<void(int done, int total)>;

class Translator {
public:
    Translator(const PipelineConfig& config, LLMClientPtr client);

    std::vector<Segment> translateSegments(
        std::vector<Segment>& segments,
        ShouldPauseFn shouldPause = nullptr,
        ProgressFn onProgress = nullptr);

private:
    QString translateOne(const Segment& seg, const QString& context);
    QString repair(const Segment& seg, const QString& badTranslation,
                   const QStringList& issues);

    PipelineConfig m_config;
    LLMClientPtr m_client;
    Masker m_masker;
    std::unique_ptr<Checkpoint> m_checkpoint;
    QHash<QString, QString> m_dedupCache;
};

} // namespace pdftransl
