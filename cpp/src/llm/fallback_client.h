#pragma once
#include "llm/base.h"
#include <vector>

namespace pdftransl {

class FallbackClient : public BaseLLMClient {
public:
    explicit FallbackClient(std::vector<LLMClientPtr> chain);

    QString chat(const std::vector<Message>& messages,
                 double temperature = 0.2,
                 std::optional<int> maxTokens = std::nullopt) override;
    bool supportsVision() const override;
    QString modelName() const override;

private:
    std::vector<LLMClientPtr> m_chain;
    int m_activeIndex = 0;
};

} // namespace pdftransl
