#pragma once
#include <QMutex>
#include <QWaitCondition>
#include <chrono>
#include <optional>

namespace pdftransl {

class RateLimiter {
public:
    explicit RateLimiter(int rpm);
    void wait();

private:
    int m_rpm;
    QMutex m_mutex;
    std::chrono::steady_clock::time_point m_windowStart;
    int m_count = 0;
};

class CooldownGate {
public:
    void wait();
    void trip(std::optional<double> retryAfter = std::nullopt);
    void reset();

private:
    QMutex m_mutex;
    QWaitCondition m_cond;
    bool m_tripped = false;
    std::chrono::steady_clock::time_point m_until;
};

} // namespace pdftransl
