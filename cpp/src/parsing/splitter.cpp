#include "parsing/splitter.h"
#include <QRegularExpression>

namespace pdftransl {

static BlockType classifyBlock(const QString& text) {
    if (text.startsWith('#')) return BlockType::Heading;
    if (text.startsWith("$$") || text.startsWith("\\begin{")) return BlockType::Math;
    if (text.startsWith("```")) return BlockType::Code;
    if (text.startsWith('|') && text.contains("---")) return BlockType::Table;
    if (text.startsWith("![")) return BlockType::Image;
    static QRegularExpression refRe(R"(^\s*\[\d+\])");
    if (refRe.match(text).hasMatch()) return BlockType::References;
    return BlockType::Text;
}

std::vector<Block> splitMarkdown(const QString& text) {
    std::vector<Block> blocks;
    static QRegularExpression blockSep(R"(\n{2,})");
    auto parts = text.split(blockSep);

    for (const auto& part : parts) {
        QString trimmed = part.trimmed();
        if (trimmed.isEmpty()) continue;
        Block b;
        b.type = classifyBlock(trimmed);
        b.text = trimmed;
        blocks.push_back(std::move(b));
    }
    return blocks;
}

QString assembleTranslation(const std::vector<Segment>& segments) {
    QStringList parts;
    parts.reserve(static_cast<int>(segments.size()));
    for (const auto& seg : segments) {
        parts.append(seg.finalText());
    }
    return parts.join("\n\n");
}

} // namespace pdftransl
