#include "quality/validators.h"
#include <QRegularExpression>

namespace pdftransl {

std::vector<QAIssue> validateSegment(const Segment& seg, const PipelineConfig& config) {
    std::vector<QAIssue> issues;
    if (seg.passthrough || seg.translation.isEmpty()) return issues;

    double ratio = static_cast<double>(seg.translation.length()) /
                   std::max(qsizetype(1), seg.sourceText.length());
    if (ratio < config.minLengthRatio)
        issues.push_back({"error", "Translation too short", "length_ratio"});
    if (ratio > config.maxLengthRatio)
        issues.push_back({"error", "Translation too long", "length_ratio"});

    // Check for leftover placeholders that weren't unmasked
    static QRegularExpression phRe(QString::fromUtf8("\xe2\x9f\xa6PH\\d+\xe2\x9f\xa7"));
    if (phRe.match(seg.translation).hasMatch())
        issues.push_back({"warning", "Unresolved placeholder in translation", "placeholder"});

    // Check residual source language
    if (config.maxResidualSourceRatio > 0) {
        auto srcWords = seg.sourceText.split(QRegularExpression(R"(\s+)"), Qt::SkipEmptyParts);
        int residual = 0;
        for (const auto& w : srcWords) {
            if (w.length() > 3 && seg.translation.contains(w, Qt::CaseInsensitive))
                residual++;
        }
        double residualRatio = srcWords.isEmpty() ? 0 :
                               static_cast<double>(residual) / srcWords.size();
        if (residualRatio > config.maxResidualSourceRatio)
            issues.push_back({"warning", "High residual source language", "residual_source"});
    }

    // LaTeX bracket balance
    if (config.fixLatex) {
        int open = seg.translation.count('{');
        int close = seg.translation.count('}');
        if (open != close)
            issues.push_back({"warning", "Unbalanced LaTeX braces", "latex_braces"});
    }

    return issues;
}

} // namespace pdftransl
