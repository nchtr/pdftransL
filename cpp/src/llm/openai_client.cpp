#include "llm/openai_client.h"
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QThread>
#include <QRandomGenerator>
#include <algorithm>
#include <cmath>

namespace pdftransl {

static const QSet<int> RETRIABLE = {408, 429, 500, 502, 503, 504};

OpenAIClient::OpenAIClient(const ProviderConfig& config,
                           RateLimiter* rateLimiter,
                           CooldownGate* cooldownGate)
    : m_config(config), m_rateLimiter(rateLimiter), m_cooldownGate(cooldownGate) {}

QString OpenAIClient::chat(const std::vector<Message>& messages,
                           double temperature, std::optional<int> maxTokens) {
    QJsonArray msgs;
    for (const auto& msg : messages) {
        msgs.append(QJsonObject{{"role", msg.role}, {"content", msg.content}});
    }
    QJsonObject payload{
        {"model", m_config.model},
        {"messages", msgs},
        {"temperature", temperature},
    };
    if (maxTokens) payload["max_tokens"] = *maxTokens;

    QString url = m_config.baseUrl.trimmed();
    if (!url.endsWith('/')) url += '/';
    url += "chat/completions";

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
        auto key = m_config.resolveApiKey();
        if (!key.isEmpty()) req.setRawHeader("Authorization", ("Bearer " + key).toUtf8());
        for (auto it = m_config.extraHeaders.begin(); it != m_config.extraHeaders.end(); ++it)
            req.setRawHeader(it.key().toUtf8(), it.value().toString().toUtf8());

        QEventLoop loop;
        auto* reply = m_nam.post(req, QJsonDocument(payload).toJson(QJsonDocument::Compact));
        QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
        loop.exec();

        int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        QByteArray body = reply->readAll();
        reply->deleteLater();

        if (reply->error() != QNetworkReply::NoError && status == 0) {
            lastError = "network error: " + reply->errorString();
            continue;
        }
        if (RETRIABLE.contains(status)) {
            auto retryHeader = reply->rawHeader("Retry-After");
            m_retryAfter = retryHeader.isEmpty() ? std::nullopt
                           : std::optional<double>(retryHeader.toDouble());
            if (status == 429 && m_cooldownGate) m_cooldownGate->trip(m_retryAfter);
            lastError = QString("HTTP %1: %2").arg(status).arg(QString::fromUtf8(body.left(300)));
            continue;
        }
        if (status != 200) {
            throw std::runtime_error(
                QString("openai HTTP %1: %2").arg(status).arg(QString::fromUtf8(body.left(500))).toStdString());
        }
        if (m_cooldownGate) m_cooldownGate->reset();

        auto doc = QJsonDocument::fromJson(body);
        auto choices = doc["choices"].toArray();
        if (choices.isEmpty()) {
            lastError = "empty choices array";
            continue;
        }
        QString text = choices[0].toObject()["message"].toObject()["content"].toString();
        if (text.trimmed().isEmpty()) {
            lastError = "empty completion";
            continue;
        }
        return text;
    }
    throw std::runtime_error(("openai: retries exhausted (" + lastError + ")").toStdString());
}

} // namespace pdftransl
