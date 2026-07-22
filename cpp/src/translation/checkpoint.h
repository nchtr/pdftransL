#pragma once
// Чекпойнт перевода документа — для возобновляемых задач. Каждый готовый
// сегмент дописывается в JSONL (append-only) рядом с исходной позицией
// (order); перезапуск подхватывает готовое и продолжает. Порт
// pdftransl/translation/checkpoint.py (упрощён: ключ — порядковый номер
// сегмента в документе вместо хеша текста, так как это уже отдельная задача
// с фиксированным списком сегментов).
#include "core/models.h"
#include <QMap>
#include <QMutex>
#include <QString>

namespace pdftransl {

// Потокобезопасный append-only журнал готовых переводов сегментов.
class Checkpoint {
public:
    explicit Checkpoint(const QString& path);

    // Дописать готовый сегмент в журнал (append; несколько потоков могут
    // звать save() параллельно).
    void save(int order, const Segment& segment);

    // Прочитать весь журнал: order -> последняя записанная версия сегмента
    // (более поздние записи в файле перекрывают более ранние для того же
    // order).
    QMap<int, Segment> load();

    // Удалить журнал (например, при полном перезапуске задачи).
    void clear();

private:
    QString m_path;
    QMutex m_mutex;
};

} // namespace pdftransl
