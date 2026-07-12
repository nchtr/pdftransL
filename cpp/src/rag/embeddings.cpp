#include "rag/embeddings.h"
#include <QCryptographicHash>
#include <QRegularExpression>
#include <cmath>

namespace pdftransl {

HashingEmbedder::HashingEmbedder(int dims) : m_dims(dims) {}

QVector<float> HashingEmbedder::embed(const QString& text) const {
    QVector<float> vec(m_dims, 0.0f);
    auto words = text.toLower().split(QRegularExpression(R"(\s+)"), Qt::SkipEmptyParts);

    for (const auto& word : words) {
        auto hash = QCryptographicHash::hash(word.toUtf8(), QCryptographicHash::Md5);
        for (int i = 0; i < m_dims; ++i) {
            uint8_t byte = static_cast<uint8_t>(hash[i % hash.size()]);
            vec[i] += (byte / 128.0f) - 1.0f;
        }
    }

    float norm = 0;
    for (float v : vec) norm += v * v;
    norm = std::sqrt(norm);
    if (norm > 0) {
        for (float& v : vec) v /= norm;
    }
    return vec;
}

float HashingEmbedder::similarity(const QVector<float>& a, const QVector<float>& b) const {
    if (a.size() != b.size()) return 0;
    float dot = 0;
    for (int i = 0; i < a.size(); ++i) dot += a[i] * b[i];
    return dot;
}

} // namespace pdftransl
