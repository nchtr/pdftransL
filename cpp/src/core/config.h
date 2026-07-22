#pragma once
#include <QString>
#include <QStringList>
#include <QVariantMap>
#include <optional>

namespace pdftransl {

struct ProviderConfig {
    QString name;
    QString baseUrl;
    QString model;
    QString apiKeyEnv;
    QString apiKey;
    QString kind = "openai"; // "openai" | "anthropic"
    bool supportsVision = false;
    bool isLocal = false;
    double timeout = 300.0;
    int maxRetries = 3;
    QVariantMap extraHeaders;

    QString resolveApiKey() const;
};

struct PipelineConfig {
    // Languages
    QString sourceLang = "en";
    QString targetLang = "ru";

    // Parsing
    QString parserBackend = "auto";
    int parserTimeout = 1800;
    bool parserFallback = true;

    // Resource guards
    bool memoryGuard = true;
    int stallWarningSeconds = 180;

    // Translation provider
    QString provider = "openrouter";
    QString model;
    QString baseUrl;
    QString apiKey;
    double temperature = 0.15;
    QStringList fallbackProviders;

    // Chunking
    int chunkCharBudget = 4000;
    int maxWorkers = 4;
    int translateBatchSize = 40;

    // Document context
    bool docSummary = true;
    bool autoGlossary = true;
    bool skipReferences = true;

    // Quality
    bool review = true;
    int maxRepairAttempts = 2;
    double minLengthRatio = 0.4;
    double maxLengthRatio = 3.5;
    double maxResidualSourceRatio = 0.35;
    bool qualityScore = false;
    bool fixLatex = true;
    bool retranslateResidual = true;
    bool unmaskedRescue = true;

    // Provider behaviour
    bool structuredOutputs = false;
    std::optional<int> rpmLimit;
    bool adaptiveThrottle = true;

    // RAG
    bool useRag = true;
    int tmTopK = 3;
    double tmMinSimilarity = 0.82;
    QString tmDomain;
    bool learn = true;

    // Figures / VLM
    bool describeFigures = false;
    QString visionProvider;
    QString visionModel;

    // OCR
    bool ocrOnScan = true;
    int ocrDpi = 200;
    int maxOcrPages = 50;

    // Output
    bool bilingual = false;
    QStringList exportFormats = {"html", "docx", "pdf"};

    // Storage
    QString dbPath = "data/pdftransl.db";
    QString outputDir = "data/output";
    bool parseCache = true;
    bool resume = true;

    static PipelineConfig fromEnv(const QVariantMap& overrides = {});
    ProviderConfig providerConfig() const;
    ProviderConfig visionProviderConfig() const;
};

ProviderConfig getProviderConfig(const QString& provider,
                                  const QString& model = {},
                                  const QString& baseUrl = {},
                                  const QString& apiKey = {});

} // namespace pdftransl
