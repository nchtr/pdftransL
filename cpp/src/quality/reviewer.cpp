#include "quality/reviewer.h"
#include "quality/validators.h"

namespace pdftransl {

void reviewSegments(std::vector<Segment>& segments, const PipelineConfig& config,
                    LLMClientPtr client) {
    for (auto& seg : segments) {
        if (seg.passthrough) continue;
        auto issues = validateSegment(seg, config);
        seg.issues.insert(seg.issues.end(), issues.begin(), issues.end());

        bool hasError = false;
        for (const auto& iss : issues) {
            if (iss.level == "error") { hasError = true; break; }
        }
        if (hasError) seg.ok = false;
    }

    if (!client || !config.qualityScore) return;

    // LLM-based review for segments with issues
    for (auto& seg : segments) {
        if (seg.ok || seg.passthrough) continue;
        std::vector<Message> msgs = {
            {"system", "You are a translation quality reviewer. Evaluate if the translation "
                       "is acceptable despite the flagged issues. Reply YES if acceptable, NO if not."},
            {"user", QString("Source: %1\nTranslation: %2\nIssues: %3")
                .arg(seg.sourceText.left(500), seg.translation.left(500),
                     [&]() { QStringList sl; for (auto& i : seg.issues) sl << i.message; return sl.join("; "); }())}
        };
        try {
            auto reply = client->chat(msgs, 0.0);
            if (reply.trimmed().toUpper().startsWith("YES"))
                seg.ok = true;
        } catch (...) {}
    }
}

} // namespace pdftransl
