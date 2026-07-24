#include "llm/anthropic_client.h"
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QThread>
#include <QTimer>
#include <QRandomGenerator>
#include <cmath>

namespace pdftransl {

static const QSet<int> RETRIABLE = {408, 429, 500, 502, 503, 504, 529};

AnthropicClient::AnthropicClient(const ProviderConfig& config,
                                 RateLimiter* rateLimiter,
                                 CooldownGate* cooldownGate)
    : m_config(config), m_rateLimiter(rateLimiter), m_cooldownGate(cooldownGate) {
    m_apiKey = config.resolveApiKey();
    if (m_apiKey.isEmpty())
        throw std::runtime_error("No API key for Anthropic provider");
}

QString AnthropicClient::chat(const std::vector<Message>& messages,
                              double temperature, std::optional<int> maxTokens) {
    QString systemPrompt;
    QJsonArray converted;
    for (const auto& msg : messages) {
        if (msg.role == "system") {
            systemPrompt = msg.content;
        } else {
            converted.append(QJsonObject{{"role", msg.role}, {"content", msg.content}});
        }
    }
    QJsonObject payload{
        {"model", m_config.model},
        {"messages", converted},
        {"temperature", temperature},
        {"max_tokens", maxTokens.value_or(8192)},
    };
    if (!systemPrompt.isEmpty()) payload["system"] = systemPrompt;

    QString url = m_config.baseUrl.trimmed();
    if (!url.endsWith('/')) url += '/';
    url += "messages";

    QString lastError;
    for (int attempt = 0; attempt <= m_config.maxRetries; ++attempt) {
        if (attempt > 0) {
            double delay = std::min(std::pow(2.0, attempt), 30.0);
            if (m_retryAfter) delay = std::max(delay, std::min(*m_retryAfter, 60.0));
            delay *= 0.75 + QRandomGenerator::global()->generateDouble() * 0.5;
            QThread::msleep(static_cast<unsigned long>(delay * 1000));
        }
        if (m_cooldownGate) m_cooldownGate->wait();
        if (m_rateLimiter) m_rateLimiter->wait();

        QNetworkRequest req{QUrl(url)};
        req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
        req.setRawHeader("x-api-key", m_apiKey.toUtf8());
        req.setRawHeader("anthropic-version", "2023-06-01");

        QEventLoop loop;
        auto* reply = m_nam.post(req, QJsonDocument(payload).toJson(QJsonDocument::Compact));
        QTimer timer;
        timer.setSingleShot(true);
        QObject::connect(&timer, &QTimer::timeout, reply, &QNetworkReply::abort);
        QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
        timer.start(static_cast<int>(m_config.timeout * 1000));
        loop.exec();
        timer.stop();

        int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        QByteArray body = reply->readAll();
        const auto networkError = reply->error();
        const QString networkErrorText = reply->errorString();
        const QByteArray retryHeader = reply->rawHeader("Retry-After");
        reply->deleteLater();

        if (networkError != QNetworkReply::NoError && status == 0) {
            lastError = "network error: " + networkErrorText;
            continue;
        }
        if (RETRIABLE.contains(status)) {
            m_retryAfter = retryHeader.isEmpty() ? std::nullopt
                           : std::optional<double>(retryHeader.toDouble());
            if ((status == 429 || status == 529) && m_cooldownGate)
                m_cooldownGate->trip(m_retryAfter);
            lastError = QString("HTTP %1: %2").arg(status).arg(QString::fromUtf8(body.left(300)));
            continue;
        }
        if (status != 200) {
            throw std::runtime_error(
                QString("anthropic HTTP %1: %2").arg(status).arg(QString::fromUtf8(body.left(500))).toStdString());
        }
        if (m_cooldownGate) m_cooldownGate->reset();

        auto doc = QJsonDocument::fromJson(body);
        auto content = doc["content"].toArray();
        QString text;
        for (const auto& block : content) {
            if (block.toObject()["type"].toString() == "text")
                text += block.toObject()["text"].toString();
        }
        if (text.trimmed().isEmpty()) {
            lastError = "empty completion";
            continue;
        }
        return text;
    }
    throw std::runtime_error(("anthropic: retries exhausted (" + lastError + ")").toStdString());
}

} // namespace pdftransl
