#pragma once
#include <QString>
#include <QJsonObject>
#include <QJsonArray>
#include <vector>
#include <optional>
#include <functional>

namespace pdftransl {

struct Message {
    QString role; // "system" | "user" | "assistant"
    QString content;
};

class BaseLLMClient {
public:
    virtual ~BaseLLMClient() = default;
    virtual QString chat(const std::vector<Message>& messages,
                         double temperature = 0.2,
                         std::optional<int> maxTokens = std::nullopt) = 0;
    virtual bool supportsVision() const { return false; }
    virtual QString modelName() const = 0;
};

using LLMClientPtr = std::shared_ptr<BaseLLMClient>;

} // namespace pdftransl
