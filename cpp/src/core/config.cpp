#include "core/config.h"
#include <QProcessEnvironment>
#include <QMap>

namespace pdftransl {

static const QMap<QString, ProviderConfig> PROVIDER_PRESETS = {
    {"openrouter", {"openrouter", "https://openrouter.ai/api/v1", "openrouter/auto",
                    "OPENROUTER_API_KEY", {}, "openai", true, false, 300.0, 3, {}}},
    {"openai", {"openai", "https://api.openai.com/v1", "gpt-4o-mini",
                "OPENAI_API_KEY", {}, "openai", true, false, 300.0, 3, {}}},
    {"anthropic", {"anthropic", "https://api.anthropic.com/v1", "claude-sonnet-5",
                   "ANTHROPIC_API_KEY", {}, "anthropic", true, false, 300.0, 3, {}}},
    {"deepseek", {"deepseek", "https://api.deepseek.com/v1", "deepseek-chat",
                  "DEEPSEEK_API_KEY", {}, "openai", false, false, 300.0, 3, {}}},
    {"ollama", {"ollama", "http://localhost:11434/v1", "qwen2.5:14b",
                {}, {}, "openai", false, true, 300.0, 3, {}}},
    {"vllm", {"vllm", "http://localhost:8000/v1", "Qwen/Qwen2.5-14B-Instruct",
              {}, {}, "openai", false, true, 300.0, 3, {}}},
    {"lmstudio", {"lmstudio", "http://localhost:1234/v1", "local-model",
                  {}, {}, "openai", false, true, 300.0, 3, {}}},
};

QString ProviderConfig::resolveApiKey() const {
    if (!apiKey.isEmpty()) return apiKey;
    if (!apiKeyEnv.isEmpty())
        return QProcessEnvironment::systemEnvironment().value(apiKeyEnv);
    return {};
}

ProviderConfig getProviderConfig(const QString& provider, const QString& model,
                                  const QString& baseUrl, const QString& apiKey) {
    ProviderConfig cfg;
    if (PROVIDER_PRESETS.contains(provider)) {
        cfg = PROVIDER_PRESETS[provider];
    } else if (!baseUrl.isEmpty()) {
        cfg.name = provider;
        cfg.baseUrl = baseUrl;
        cfg.model = model;
    } else {
        cfg.name = provider;
        cfg.baseUrl = "http://localhost:11434/v1";
        cfg.model = model.isEmpty() ? "local-model" : model;
        cfg.isLocal = true;
    }
    if (!model.isEmpty()) cfg.model = model;
    if (!baseUrl.isEmpty()) cfg.baseUrl = baseUrl;
    if (!apiKey.isEmpty()) cfg.apiKey = apiKey;
    return cfg;
}

PipelineConfig PipelineConfig::fromEnv(const QVariantMap& overrides) {
    PipelineConfig cfg;
    auto env = QProcessEnvironment::systemEnvironment();

    auto str = [&](const QString& envName, QString& field) {
        if (env.contains(envName)) field = env.value(envName);
    };
    auto boolean = [&](const QString& envName, bool& field) {
        if (env.contains(envName)) {
            auto v = env.value(envName).toLower().trimmed();
            field = (v == "1" || v == "true" || v == "yes" || v == "on");
        }
    };
    auto integer = [&](const QString& envName, int& field) {
        if (env.contains(envName)) field = env.value(envName).toInt();
    };

    str("PDFTRANSL_SOURCE_LANG", cfg.sourceLang);
    str("PDFTRANSL_TARGET_LANG", cfg.targetLang);
    str("PDFTRANSL_PARSER", cfg.parserBackend);
    str("PDFTRANSL_PROVIDER", cfg.provider);
    str("PDFTRANSL_MODEL", cfg.model);
    str("PDFTRANSL_BASE_URL", cfg.baseUrl);
    str("PDFTRANSL_DB", cfg.dbPath);
    str("PDFTRANSL_OUTPUT_DIR", cfg.outputDir);
    str("PDFTRANSL_VISION_PROVIDER", cfg.visionProvider);
    str("PDFTRANSL_VISION_MODEL", cfg.visionModel);

    boolean("PDFTRANSL_REVIEW", cfg.review);
    boolean("PDFTRANSL_USE_RAG", cfg.useRag);
    boolean("PDFTRANSL_LEARN", cfg.learn);
    boolean("PDFTRANSL_DESCRIBE_FIGURES", cfg.describeFigures);
    boolean("PDFTRANSL_DOC_SUMMARY", cfg.docSummary);
    boolean("PDFTRANSL_AUTO_GLOSSARY", cfg.autoGlossary);
    boolean("PDFTRANSL_SKIP_REFERENCES", cfg.skipReferences);
    boolean("PDFTRANSL_BILINGUAL", cfg.bilingual);
    boolean("PDFTRANSL_PARSE_CACHE", cfg.parseCache);
    boolean("PDFTRANSL_QUALITY_SCORE", cfg.qualityScore);
    boolean("PDFTRANSL_FIX_LATEX", cfg.fixLatex);
    boolean("PDFTRANSL_OCR_ON_SCAN", cfg.ocrOnScan);
    boolean("PDFTRANSL_PARSER_FALLBACK", cfg.parserFallback);
    boolean("PDFTRANSL_ADAPTIVE_THROTTLE", cfg.adaptiveThrottle);
    boolean("PDFTRANSL_RESUME", cfg.resume);
    boolean("PDFTRANSL_MEMORY_GUARD", cfg.memoryGuard);

    integer("PDFTRANSL_MAX_WORKERS", cfg.maxWorkers);
    integer("PDFTRANSL_TRANSLATE_BATCH_SIZE", cfg.translateBatchSize);
    integer("PDFTRANSL_PARSER_TIMEOUT", cfg.parserTimeout);
    integer("PDFTRANSL_OCR_DPI", cfg.ocrDpi);

    if (env.contains("PDFTRANSL_RPM")) cfg.rpmLimit = env.value("PDFTRANSL_RPM").toInt();
    if (env.contains("PDFTRANSL_FALLBACK_PROVIDERS")) {
        cfg.fallbackProviders = env.value("PDFTRANSL_FALLBACK_PROVIDERS")
            .split(",", Qt::SkipEmptyParts);
        for (auto& p : cfg.fallbackProviders) p = p.trimmed();
    }
    if (env.contains("PDFTRANSL_EXPORT_FORMATS")) {
        cfg.exportFormats = env.value("PDFTRANSL_EXPORT_FORMATS")
            .split(",", Qt::SkipEmptyParts);
        for (auto& f : cfg.exportFormats) f = f.trimmed();
    }

    // Apply overrides
    for (auto it = overrides.begin(); it != overrides.end(); ++it) {
        const auto& key = it.key();
        const auto& val = it.value();
        if (key == "source_lang") cfg.sourceLang = val.toString();
        else if (key == "target_lang") cfg.targetLang = val.toString();
        else if (key == "provider") cfg.provider = val.toString();
        else if (key == "model") cfg.model = val.toString();
        else if (key == "max_workers") cfg.maxWorkers = val.toInt();
        else if (key == "review") cfg.review = val.toBool();
        else if (key == "use_rag") cfg.useRag = val.toBool();
        else if (key == "parser_backend") cfg.parserBackend = val.toString();
    }
    return cfg;
}

ProviderConfig PipelineConfig::providerConfig() const {
    return getProviderConfig(provider, model, baseUrl, apiKey);
}

ProviderConfig PipelineConfig::visionProviderConfig() const {
    auto vp = visionProvider.isEmpty() ? provider : visionProvider;
    auto vm = visionModel.isEmpty() ? model : visionModel;
    auto cfg = getProviderConfig(vp, vm,
        visionProvider.isEmpty() ? baseUrl : QString{},
        visionProvider.isEmpty() ? apiKey : QString{});
    if (!visionModel.isEmpty() || !visionProvider.isEmpty())
        cfg.supportsVision = true;
    return cfg;
}

} // namespace pdftransl
