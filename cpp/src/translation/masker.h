#pragma once
// Маскировка непереводимых фрагментов плейсхолдерами вида ⟦PH0⟧, ⟦PH1⟧.
//
// Научный Markdown полон LaTeX-формул, кода, ссылок на картинки, цитирований
// и URL — всё это должно пережить перевод байт-в-байт. Перед отправкой
// сегмента в LLM каждый такой фрагмент заменяется на непрозрачный токен;
// после перевода токены подставляются обратно, а их целостность проверяется
// (потерянный токен = потерянная формула — жёсткая ошибка, запускающая цикл
// исправлений). Порт pdftransl/masking.py.
#include <QMap>
#include <QMutex>
#include <QString>
#include <QStringList>

namespace pdftransl {

struct MaskResult {
    QString text;                        // текст с плейсхолдерами вместо защищённых фрагментов
    QMap<QString, QString> placeholders; // token ("⟦PH0⟧") -> original fragment
};

struct UnmaskResult {
    QString text;         // восстановленный текст
    QStringList missing;  // плейсхолдеры из mapping, отсутствующие в переводе
    QStringList unknown;  // похожие на плейсхолдер токены, которых не было в mapping
};

// Маскер с внутренним счётчиком: плейсхолдеры уникальны в рамках одного
// документа (одного экземпляра Masker), даже если mask() вызывается из
// нескольких потоков параллельно — метод потокобезопасен.
class Masker {
public:
    explicit Masker(qint64 start = 0);

    MaskResult mask(const QString& text);

private:
    QMutex m_mutex;
    qint64 m_counter;
};

// Восстановить плейсхолдеры в переводе. Делает несколько проходов до
// неподвижной точки (вложенные маскировки раскрываются послойно) и
// дополнительный "нечёткий" проход, распознающий слегка искажённые моделью
// токены (другие скобки, лишние пробелы, кириллические гомоглифы P/H).
UnmaskResult unmask(const QString& text, const QMap<QString, QString>& placeholders);

// Убрать плейсхолдеры из текста (для статистики по языку/длине) — заменяет
// каждый токен на один пробел.
QString stripPlaceholders(const QString& text);

} // namespace pdftransl
