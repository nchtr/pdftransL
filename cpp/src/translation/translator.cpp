#include "translation/translator.h"
#include "translation/prompts.h"
#include <QtConcurrent>
#include <QFutureSynchronizer>
#include <QMutex>

namespace pdftransl {

Translator::Translator(const PipelineConfig& config, LLMClientPtr client)
    : m_config(config), m_client(std::move(client)) {
    if (m_config.resume) {
        QString cpPath = m_config.outputDir + "/.checkpoint.jsonl";
        m_checkpoint = std::make_unique<Checkpoint>(cpPath);
    }
}

std::vector<Segment> Translator::translateSegments(
    std::vector<Segment>& segments, ShouldPauseFn shouldPause, ProgressFn onProgress) {

    QMap<int, Segment> cached;
    if (m_checkpoint) cached = m_checkpoint->load();

    int total = static_cast<int>(segments.size());
    std::atomic<int> done{0};
    QMutex mu;
    QString rollingContext;

    auto processBatch = [&](int start, int end) {
        for (int i = start; i < end; ++i) {
            if (shouldPause && shouldPause()) return;

            auto& seg = segments[i];
            if (seg.passthrough) {
                done++;
                if (onProgress) onProgress(done, total);
                continue;
            }
            if (cached.contains(i)) {
                seg = cached[i];
                done++;
                if (onProgress) onProgress(done, total);
                continue;
            }

            // Mask if not already masked
            if (seg.maskedText.isEmpty()) {
                auto maskResult = m_masker.mask(seg.sourceText);
                seg.maskedText = maskResult.text;
                seg.placeholders = maskResult.placeholders;
            }

            QString ctx;
            {
                QMutexLocker lock(&mu);
                ctx = rollingContext;
            }

            if (m_dedupCache.contains(seg.maskedText)) {
                seg.translation = m_dedupCache[seg.maskedText];
            } else {
                seg.translation = translateOne(seg, ctx);

                for (int repair = 0; repair < m_config.maxRepairAttempts; ++repair) {
                    QStringList issues;
                    double ratio = seg.translation.length() /
                                   std::max(1.0, static_cast<double>(seg.sourceText.length()));
                    if (ratio < m_config.minLengthRatio)
                        issues << "Translation too short";
                    if (ratio > m_config.maxLengthRatio)
                        issues << "Translation too long";

                    if (issues.isEmpty()) break;
                    seg.translation = this->repair(seg, seg.translation, issues);
                }

                QMutexLocker lock(&mu);
                m_dedupCache[seg.maskedText] = seg.translation;
            }

            {
                QMutexLocker lock(&mu);
                rollingContext = seg.translation.right(200);
            }

            // Unmask placeholders in the translation
            auto unmasked = unmask(seg.translation, seg.placeholders);
            seg.translation = unmasked.text;
            if (!unmasked.missing.isEmpty()) {
                seg.issues.push_back({"warning",
                    "Missing placeholders: " + unmasked.missing.join(", "),
                    "placeholder_missing"});
            }

            if (m_checkpoint) m_checkpoint->save(i, seg);
            done++;
            if (onProgress) onProgress(done, total);
        }
    };

    int batchSize = m_config.translateBatchSize;
    for (int start = 0; start < total; start += batchSize) {
        if (shouldPause && shouldPause()) break;
        int end = std::min(start + batchSize, total);

        int workers = std::min(m_config.maxWorkers, end - start);
        if (workers <= 1) {
            processBatch(start, end);
        } else {
            int chunkSize = (end - start + workers - 1) / workers;
            QFutureSynchronizer<void> sync;
            for (int w = 0; w < workers; ++w) {
                int s = start + w * chunkSize;
                int e = std::min(s + chunkSize, end);
                if (s >= end) break;
                sync.addFuture(QtConcurrent::run(processBatch, s, e));
            }
            sync.waitForFinished();
        }
    }

    return segments;
}

QString Translator::translateOne(const Segment& seg, const QString& context) {
    auto sys = buildTranslationSystem(m_config.sourceLang, m_config.targetLang, context);
    auto user = buildUserMessage(seg.maskedText);
    std::vector<Message> msgs = {
        {"system", sys},
        {"user", user}
    };
    return m_client->chat(msgs, m_config.temperature);
}

QString Translator::repair(const Segment& seg, const QString& badTranslation,
                           const QStringList& issues) {
    auto sys = buildTranslationSystem(m_config.sourceLang, m_config.targetLang);
    QString userMsg = REPAIR_USER.arg(issues.join("\n- "), seg.maskedText);
    std::vector<Message> msgs = {
        {"system", sys},
        {"assistant", badTranslation},
        {"user", userMsg}
    };
    return m_client->chat(msgs, m_config.temperature);
}

} // namespace pdftransl
