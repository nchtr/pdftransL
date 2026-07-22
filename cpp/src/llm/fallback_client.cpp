#include "llm/fallback_client.h"
#include <stdexcept>

namespace pdftransl {

FallbackClient::FallbackClient(std::vector<LLMClientPtr> chain)
    : m_chain(std::move(chain)) {
    if (m_chain.empty())
        throw std::runtime_error("FallbackClient requires at least one provider");
}

QString FallbackClient::chat(const std::vector<Message>& messages,
                             double temperature, std::optional<int> maxTokens) {
    // sticky: try the current provider first, then fall through
    for (size_t i = 0; i < m_chain.size(); ++i) {
        size_t idx = (m_activeIndex + i) % m_chain.size();
        try {
            auto result = m_chain[idx]->chat(messages, temperature, maxTokens);
            m_activeIndex = static_cast<int>(idx);
            return result;
        } catch (const std::exception&) {
            if (i == m_chain.size() - 1) throw; // last one, propagate
        }
    }
    throw std::runtime_error("all providers exhausted");
}

bool FallbackClient::supportsVision() const {
    return !m_chain.empty() && m_chain[m_activeIndex]->supportsVision();
}

QString FallbackClient::modelName() const {
    return m_chain.empty() ? "none" : m_chain[m_activeIndex]->modelName();
}

} // namespace pdftransl
