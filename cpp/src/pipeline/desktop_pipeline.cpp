#include "pipeline/desktop_pipeline.h"

#include "export/exporter.h"
#include "llm/anthropic_client.h"
#include "llm/fallback_client.h"
#include "llm/openai_client.h"
#include "llm/ratelimit.h"
#include "parsing/splitter.h"
#include "parsing/subprocess_backend.h"
#include "translation/checkpoint.h"
#include "translation/translator.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <utility>
#include <vector>

namespace pdftransl {

namespace {

std::unique_ptr<ParserBackend> makeParser(const PipelineConfig& config) {
    auto make = [&config](SubprocessBackendSpec spec) {
        spec.timeoutSeconds = config.parserTimeout;
        return std::make_unique<SubprocessBackend>(spec);
    };
    if (config.parserBackend == "marker") return make(markerBackendSpec());
    if (config.parserBackend == "nougat") return make(nougatBackendSpec());
    if (config.parserBackend == "docling") return make(doclingBackendSpec());
    if (config.parserBackend != "auto") {
        throw std::runtime_error(
            (QStringLiteral("unsupported desktop parser: ") + config.parserBackend).toStdString());
    }
    for (const auto& spec : {markerBackendSpec(), nougatBackendSpec(), doclingBackendSpec()}) {
        auto parser = make(spec);
        if (parser->available()) return parser;
    }
    throw std::runtime_error("no supported PDF parser found in PATH (marker, nougat, docling)");
}

LLMClientPtr makeOneClient(const ProviderConfig& config, RateLimiter* limiter,
                           CooldownGate* cooldown) {
    if (config.kind == "anthropic") {
        return std::make_shared<AnthropicClient>(config, limiter, cooldown);
    }
    return std::make_shared<OpenAIClient>(config, limiter, cooldown);
}

LLMClientPtr makeClient(const PipelineConfig& config, RateLimiter* limiter,
                        CooldownGate* cooldown) {
    std::vector<LLMClientPtr> chain;
    chain.push_back(makeOneClient(config.providerConfig(), limiter, cooldown));
    for (const auto& fallback : config.fallbackProviders) {
        if (fallback != config.provider) {
            chain.push_back(makeOneClient(getProviderConfig(fallback), limiter, cooldown));
        }
    }
    return chain.size() == 1 ? chain.front() : std::make_shared<FallbackClient>(std::move(chain));
}

} // namespace

DesktopPipeline::DesktopPipeline(PipelineConfig config) : m_config(std::move(config)) {}

JobResult DesktopPipeline::run(const QString& pdfPath, const QString& jobId,
                               StageProgressFn onStage, ShouldPauseFn shouldPause) const {
    JobResult result;
    result.jobId = jobId;
    try {
        const QFileInfo input(pdfPath);
        if (!input.exists()) throw std::runtime_error("input PDF no longer exists");
        const QString outDir = QDir(m_config.outputDir).filePath(input.completeBaseName() + "_" + jobId);
        QDir().mkpath(outDir);

        if (onStage) onStage("parse", 0.0);
        auto parser = makeParser(m_config);
        ParsedDocument parsed = parser->parse(pdfPath, QDir(outDir).filePath("parse"));
        if (shouldPause && shouldPause()) {
            result.status = "paused";
            return result;
        }
        if (onStage) onStage("translate", 0.05);

        auto blocks = splitMarkdown(parsed.markdown);
        if (m_config.skipReferences) blocks = markReferences(blocks);
        auto segments = toSegments(blocks);

        std::unique_ptr<RateLimiter> limiter;
        if (m_config.rpmLimit) limiter = std::make_unique<RateLimiter>(*m_config.rpmLimit);
        CooldownGate cooldown;
        auto client = makeClient(m_config, limiter.get(), m_config.adaptiveThrottle ? &cooldown : nullptr);
        Translator translator(m_config, client);
        translator.setCheckpoint(std::make_shared<Checkpoint>(QDir(outDir).filePath("checkpoint.jsonl")));
        result.segments = translator.translateSegments(
            segments, shouldPause,
            [&onStage](int done, int total) {
                if (onStage) onStage("translate", total ? 0.05 + 0.8 * double(done) / total : 0.85);
            });

        const bool paused = shouldPause && shouldPause();
        const QString markdown = assemble(result.segments);
        const QString mdPath = QDir(outDir).filePath(input.completeBaseName() + "." + m_config.targetLang + ".md");
        QFile mdFile(mdPath);
        if (!mdFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
            throw std::runtime_error((QStringLiteral("cannot write ") + mdPath).toStdString());
        }
        mdFile.write(markdown.toUtf8());
        result.outputMarkdownPath = mdPath;

        if (paused) {
            result.status = "paused";
            return result;
        }
        if (onStage) onStage("export", 0.9);
        auto exported = exportDocument(markdown, QDir(outDir).filePath(input.completeBaseName()),
                                       m_config.exportFormats, QFileInfo(parsed.markdownPath).absolutePath(),
                                       input.completeBaseName());
        result.exports = exported.value("files").toMap();
        result.report["export_engines"] = exported.value("engines").toMap();
        bool allOk = true;
        for (const auto& segment : result.segments) allOk = allOk && segment.ok;
        result.status = allOk ? "completed" : "partial";
        if (onStage) onStage("export", 1.0);
    } catch (const std::exception& exc) {
        result.status = "failed";
        result.error = QString::fromUtf8(exc.what());
    }
    return result;
}

} // namespace pdftransl
