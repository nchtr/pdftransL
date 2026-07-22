#include "llm/ratelimit.h"
#include <QThread>
#include <algorithm>

namespace pdftransl {

RateLimiter::RateLimiter(int rpm)
    : m_rpm(rpm), m_windowStart(std::chrono::steady_clock::now()) {}

void RateLimiter::wait() {
    QMutexLocker lock(&m_mutex);
    auto now = std::chrono::steady_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - m_windowStart);
    if (elapsed.count() >= 60000) {
        m_windowStart = now;
        m_count = 0;
    }
    if (m_count >= m_rpm) {
        auto sleepMs = 60000 - elapsed.count();
        if (sleepMs > 0) {
            lock.unlock();
            QThread::msleep(static_cast<unsigned long>(sleepMs));
            lock.relock();
        }
        m_windowStart = std::chrono::steady_clock::now();
        m_count = 0;
    }
    ++m_count;
}

void CooldownGate::wait() {
    QMutexLocker lock(&m_mutex);
    while (m_tripped) {
        auto now = std::chrono::steady_clock::now();
        if (now >= m_until) {
            m_tripped = false;
            break;
        }
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(m_until - now).count();
        m_cond.wait(&m_mutex, static_cast<unsigned long>(ms));
    }
}

void CooldownGate::trip(std::optional<double> retryAfter) {
    QMutexLocker lock(&m_mutex);
    double delay = retryAfter.value_or(5.0);
    delay = std::clamp(delay, 1.0, 120.0);
    m_tripped = true;
    m_until = std::chrono::steady_clock::now() +
              std::chrono::milliseconds(static_cast<int>(delay * 1000));
}

void CooldownGate::reset() {
    QMutexLocker lock(&m_mutex);
    m_tripped = false;
    m_cond.wakeAll();
}

} // namespace pdftransl
