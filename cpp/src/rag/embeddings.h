#pragma once
// Хэширующий эмбеддер без внешних зависимостей: детерминированные
// псевдо-эмбеддинги через хеширование символьных n-грамм (SHA-256).
// Работает офлайн и без ML-библиотек — этого достаточно для приблизительного
// косинусного поиска в памяти переводов. Порт идеи HashingEmbedder из
// pdftransl/rag/embeddings.py (там — MD5, 512 измерений; здесь — SHA-256 и
// 128 по заданию).
#include <QString>
#include <vector>

namespace pdftransl {

class HashingEmbedder {
public:
    explicit HashingEmbedder(int dim = 128);

    // Вектор единичной длины (L2-нормализованный) размерности dim().
    std::vector<float> embed(const QString& text) const;

    int dim() const { return m_dim; }

private:
    int m_dim;
};

// Косинусное сходство двух векторов одинаковой размерности; 0.0, если
// размерности не совпадают или один из векторов нулевой.
double cosine(const std::vector<float>& a, const std::vector<float>& b);

} // namespace pdftransl
