#include "parsing/splitter.h"
#include <QRegularExpression>
#include <optional>

namespace pdftransl {

namespace {

const QRegularExpression& headingRe() {
    static const QRegularExpression re(QStringLiteral("^#{1,6}\\s"));
    return re;
}
const QRegularExpression& imageOnlyRe() {
    static const QRegularExpression re(QStringLiteral("^\\s*!\\[[^\\]]*\\]\\([^)]*\\)\\s*$"));
    return re;
}
const QRegularExpression& tableRowRe() {
    static const QRegularExpression re(QStringLiteral("^\\s*\\|"));
    return re;
}
const QRegularExpression& fenceRe() {
    static const QRegularExpression re(QStringLiteral("^\\s*(```|~~~)"));
    return re;
}
const QRegularExpression& displayMathLineRe() {
    static const QRegularExpression re(QStringLiteral("^\\s*\\$\\$"));
    return re;
}
const QRegularExpression& htmlBlockRe() {
    static const QRegularExpression re(
        QStringLiteral("^\\s*</?(?:div|table|tr|td|th|img|figure|p|span|br|hr)\\b"),
        QRegularExpression::CaseInsensitiveOption);
    return re;
}
const QRegularExpression& beginEnvRe() {
    static const QRegularExpression re(QStringLiteral("^\\s*\\\\begin\\{([a-zA-Z*]+)\\}"));
    return re;
}
const QRegularExpression& referencesHeadingRe() {
    static const QRegularExpression re(
        QStringLiteral("^#{1,6}\\s*(?:\\d+\\.?\\s*)?(references|bibliography|"
                        "список литературы|литература|cited works|works cited)\\s*$"),
        QRegularExpression::CaseInsensitiveOption);
    return re;
}

QString normalizeNewlines(const QString& text) {
    QString out = text;
    out.replace(QStringLiteral("\r\n"), QStringLiteral("\n"));
    out.replace(QChar('\r'), QChar('\n'));
    return out;
}

} // namespace

bool isTranslatableType(BlockType type) {
    return type == BlockType::Text || type == BlockType::Heading || type == BlockType::Table;
}

std::vector<Block> splitMarkdown(const QString& markdown) {
    std::vector<Block> blocks;
    const QStringList lines = normalizeNewlines(markdown).split('\n');

    QStringList buf;
    std::optional<BlockType> bufType;
    QString fenceMarker;   // непусто, пока мы внутри ``` / ~~~ ограды
    bool mathOpen = false; // внутри многострочного $$ ... $$
    QString envName;       // непусто, пока мы внутри \begin{name} ... \end{name}

    auto flush = [&](std::optional<BlockType> forced = std::nullopt) {
        if (!buf.isEmpty()) {
            Block block;
            block.type = forced ? *forced : bufType.value_or(BlockType::Text);
            block.text = buf.join('\n');
            block.page = 0;
            blocks.push_back(std::move(block));
        }
        buf.clear();
        bufType.reset();
    };

    for (const QString& line : lines) {
        // --- многострочные состояния -------------------------------
        if (!fenceMarker.isEmpty()) {
            buf << line;
            if (line.trimmed().startsWith(fenceMarker)) {
                fenceMarker.clear();
                flush(BlockType::Code);
            }
            continue;
        }
        if (mathOpen) {
            buf << line;
            if (line.contains(QStringLiteral("$$"))) {
                mathOpen = false;
                flush(BlockType::Math);
            }
            continue;
        }
        if (!envName.isEmpty()) {
            buf << line;
            QRegularExpression endRe(QStringLiteral("\\\\end\\{") + QRegularExpression::escape(envName) +
                                      QStringLiteral("\\}"));
            if (endRe.match(line).hasMatch()) {
                envName.clear();
                flush(BlockType::Math);
            }
            continue;
        }

        const QString stripped = line.trimmed();

        // --- открытие многострочных состояний -----------------------
        QRegularExpressionMatch fenceMatch = fenceRe().match(line);
        if (fenceMatch.hasMatch()) {
            flush();
            buf << line;
            fenceMarker = fenceMatch.captured(1);
            continue;
        }
        if (displayMathLineRe().match(line).hasMatch()) {
            flush();
            buf << line;
            if (stripped.count(QStringLiteral("$$")) >= 2) {
                flush(BlockType::Math);
            } else {
                mathOpen = true;
            }
            continue;
        }
        QRegularExpressionMatch envMatch = beginEnvRe().match(line);
        if (envMatch.hasMatch()) {
            flush();
            buf << line;
            const QString name = envMatch.captured(1);
            QRegularExpression endRe(QStringLiteral("\\\\end\\{") + QRegularExpression::escape(name) +
                                      QStringLiteral("\\}"));
            if (endRe.match(line).hasMatch()) {
                flush(BlockType::Math);
            } else {
                envName = name;
            }
            continue;
        }

        // --- однострочные / группируемые конструкции -----------------
        if (stripped.isEmpty()) {
            flush();
            continue;
        }
        if (headingRe().match(line).hasMatch()) {
            flush();
            buf << line;
            flush(BlockType::Heading);
            continue;
        }
        if (imageOnlyRe().match(line).hasMatch()) {
            flush();
            buf << line;
            flush(BlockType::Image);
            continue;
        }
        if (tableRowRe().match(line).hasMatch()) {
            if (bufType != BlockType::Table) flush();
            bufType = BlockType::Table;
            buf << line;
            continue;
        }
        if (buf.isEmpty() && !bufType.has_value() && htmlBlockRe().match(line).hasMatch()) {
            buf << line;
            flush(BlockType::Html);
            continue;
        }

        // умолчание: текст абзаца
        if (bufType == BlockType::Table) flush();
        if (!bufType.has_value()) bufType = BlockType::Text;
        buf << line;
    }
    flush();
    return blocks;
}

std::vector<Block> markReferences(const std::vector<Block>& blocksIn, int* outMarked) {
    std::vector<Block> blocks = blocksIn;
    int marked = 0;
    bool inRefs = false;
    for (auto& block : blocks) {
        if (block.type == BlockType::Heading) {
            inRefs = referencesHeadingRe().match(block.text.trimmed()).hasMatch();
        } else if (inRefs && isTranslatableType(block.type)) {
            block.type = BlockType::References;
            ++marked;
        }
    }
    if (outMarked) *outMarked = marked;
    return blocks;
}

std::vector<Segment> toSegments(const std::vector<Block>& blocks) {
    std::vector<Segment> segments;
    segments.reserve(blocks.size());
    for (const auto& block : blocks) {
        Segment segment;
        segment.id = newId("seg_");
        segment.kind = block.type;
        segment.sourceText = block.text;
        segment.passthrough = !isTranslatableType(block.type) || block.text.trimmed().isEmpty();
        segments.push_back(std::move(segment));
    }
    return segments;
}

QString assemble(const std::vector<QString>& texts) {
    QStringList nonEmpty;
    nonEmpty.reserve(static_cast<int>(texts.size()));
    for (const auto& text : texts) {
        if (!text.trimmed().isEmpty()) nonEmpty << text;
    }
    return nonEmpty.join(QStringLiteral("\n\n")) + QStringLiteral("\n");
}

QString assemble(const std::vector<Segment>& segments) {
    std::vector<QString> texts;
    texts.reserve(segments.size());
    for (const auto& segment : segments) texts.push_back(segment.finalText());
    return assemble(texts);
}

} // namespace pdftransl
