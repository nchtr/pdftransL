#pragma once
#include <QString>
#include <QVector>

namespace pdftransl {

class HashingEmbedder {
public:
    explicit HashingEmbedder(int dims = 128);
    QVector<float> embed(const QString& text) const;
    float similarity(const QVector<float>& a, const QVector<float>& b) const;

private:
    int m_dims;
};

} // namespace pdftransl
