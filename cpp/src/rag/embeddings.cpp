#include "rag/embeddings.h"
#include <QCryptographicHash>
#include <QRegularExpression>
#include <cmath>

namespace pdftransl {

namespace {

const QRegularExpression& tokenRe() {
    // Unicode-буквенно-цифровые последовательности (латиница, кириллица,
    // CJK...); \W/\d исключают пунктуацию и цифры-как-разделители, как в
    // pdftransl.rag.embeddings._TOKEN_RE.
    static const QRegularExpression re(QStringLiteral("[^\\W\\d_]+"),
                                        QRegularExpression::UseUnicodePropertiesOption);
    return re;
}

} // namespace

HashingEmbedder::HashingEmbedder(int dim) : m_dim(dim > 0 ? dim : 128) {}

std::vector<float> HashingEmbedder::embed(const QString& text) const {
    std::vector<float> vec(static_cast<size_t>(m_dim), 0.0f);

    QRegularExpressionMatchIterator tokens = tokenRe().globalMatch(text.toLower());
    while (tokens.hasNext()) {
        const QString token = tokens.next().captured(0);
        const QString padded = QStringLiteral("^") + token + QStringLiteral("$");
        for (int i = 0; i + 3 <= padded.size(); ++i) {
            const QString gram = padded.mid(i, 3);
            const QByteArray digest =
                QCryptographicHash::hash(gram.toUtf8(), QCryptographicHash::Sha256);
            // Первые 4 байта дайджеста -> индекс корзины; следующий байт ->
            // знак вклада (детерминированный псевдослучайный проекционный
            // хеш, как в hashing trick / feature hashing).
            quint32 raw = 0;
            for (int b = 0; b < 4; ++b) {
                raw |= static_cast<quint32>(static_cast<unsigned char>(digest[b])) << (8 * b);
            }
            const int idx = static_cast<int>(raw % static_cast<quint32>(m_dim));
            const float sign = (static_cast<unsigned char>(digest[4]) % 2) ? 1.0f : -1.0f;
            vec[static_cast<size_t>(idx)] += sign;
        }
    }

    double norm = 0.0;
    for (float v : vec) norm += static_cast<double>(v) * v;
    norm = std::sqrt(norm);
    if (norm > 0.0) {
        for (float& v : vec) v = static_cast<float>(v / norm);
    }
    return vec;
}

double cosine(const std::vector<float>& a, const std::vector<float>& b) {
    if (a.empty() || b.empty() || a.size() != b.size()) return 0.0;
    double dot = 0.0, na = 0.0, nb = 0.0;
    for (size_t i = 0; i < a.size(); ++i) {
        dot += static_cast<double>(a[i]) * b[i];
        na += static_cast<double>(a[i]) * a[i];
        nb += static_cast<double>(b[i]) * b[i];
    }
    na = std::sqrt(na);
    nb = std::sqrt(nb);
    if (na <= 0.0 || nb <= 0.0) return 0.0;
    return dot / (na * nb);
}

} // namespace pdftransl
