#pragma once
// Движок перевода: маскировка, параллельный LLM-перевод сегментов партиями
// и ограниченный цикл самопочинки по замечаниям валидаторов. Порт
// pdftransl/translation/translator.py (без внутренней сегментации по
// char_budget — сегменты уже готовы к переводу на входе; см.
// parsing::toSegments).
#include "core/config.h"
#include "core/models.h"
#include "llm/base.h"
#include "translation/checkpoint.h"
#include "translation/masker.h"
#include <QHash>
#include <QMap>
#include <QMutex>
#include <QStringList>
#include <functional>
#include <memory>
#include <vector>

namespace pdftransl {

using ShouldPauseFn = std::function<bool()>;
using ProgressFn = std::function<void(int done, int total)>;

class Translator {
public:
    Translator(const PipelineConfig& config, LLMClientPtr client);

    // Документный контекст (саммари для системного промпта и глоссарий,
    // принудительно подмешиваемый в перевод) — пайплайн задаёт его один раз
    // на документ, до вызова translateSegments().
    void setDocumentContext(const QString& docSummary, const QStringList& glossaryHints = {});

    // Подключить чекпойнт для возобновляемых задач (необязательно): готовые
    // сегменты подставляются без обращения к LLM, новые — дописываются по
    // мере перевода.
    void setCheckpoint(std::shared_ptr<Checkpoint> checkpoint);

    // Перевести все непроходные (passthrough == false) сегменты; passthrough
    // возвращаются без изменений. Сегменты переводятся партиями по
    // config.translateBatchSize через общий пул из config.maxWorkers потоков.
    // shouldPause опрашивается перед каждой партией; сегменты, до которых
    // пауза не дошла, помечаются предупреждением "paused" и остаются
    // непереведёнными (translation остаётся пустым, finalText() откатится на
    // оригинал) — их подхватит чекпойнт при возобновлении. onProgress(done,
    // total) вызывается после каждой партии.
    std::vector<Segment> translateSegments(
        const std::vector<Segment>& segments,
        ShouldPauseFn shouldPause = nullptr,
        ProgressFn onProgress = nullptr);

private:
    Segment translateOne(Segment segment, const QString& sourceContext);
    void finalizeAttempt(Segment& segment, const QString& rawTranslation,
                          const QMap<QString, QString>& placeholders);
    static QString issuesToText(const std::vector<QAIssue>& issues);

    PipelineConfig m_config;
    LLMClientPtr m_client;
    Masker m_masker;
    std::shared_ptr<Checkpoint> m_checkpoint;

    QString m_docSummary;
    QStringList m_docGlossaryHints;

    // Кэш повторяющихся сегментов на время одного документа: колонтитулы и
    // повторные подписи переводятся один раз, копии переиспользуют готовый
    // текст. Ключ — исходный текст сегмента, значение — итоговый (уже
    // восстановленный из плейсхолдеров) перевод.
    QMutex m_cacheMutex;
    QHash<QString, QString> m_runCache;
    // QNetworkAccessManager (inside the concrete clients) is thread-affine.
    // Segments may be prepared in parallel, but network requests through one
    // shared client must be serialized.
    QMutex m_clientMutex;
};

} // namespace pdftransl
