#pragma once
#include "llm/base.h"
#include "llm/ratelimit.h"
#include "core/config.h"
#include <QNetworkAccessManager>
#include <memory>

namespace pdftransl {

class OpenAIClient : public BaseLLMClient {
public:
    OpenAIClient(const ProviderConfig& config,
                 RateLimiter* rateLimiter = nullptr,
                 CooldownGate* cooldownGate = nullptr);

    QString chat(const std::vector<Message>& messages,
                 double temperature = 0.2,
                 std::optional<int> maxTokens = std::nullopt) override;
    bool supportsVision() const override { return m_config.supportsVision; }
    QString modelName() const override { return m_config.model; }

private:
    ProviderConfig m_config;
    RateLimiter* m_rateLimiter;
    CooldownGate* m_cooldownGate;
    QNetworkAccessManager m_nam;
    std::optional<double> m_retryAfter;
};

} // namespace pdftransl
