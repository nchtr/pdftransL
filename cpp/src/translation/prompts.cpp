#include "translation/prompts.h"
#include <QMap>

namespace pdftransl {

namespace {
const QMap<QString, QString>& langNames() {
    static const QMap<QString, QString> names = {
        {"en", "English"}, {"ru", "Russian"}, {"de", "German"},
        {"fr", "French"}, {"es", "Spanish"}, {"zh", "Chinese"},
        {"ja", "Japanese"}, {"uk", "Ukrainian"}, {"kk", "Kazakh"},
    };
    return names;
}
} // namespace

QString langName(const QString& code) {
    return langNames().value(code.toLower(), code);
}

const QString REPAIR_USER = QStringLiteral(
    "Your previous translation has problems that must be fixed:\n"
    "%1\n\n"
    "Source text (with placeholders):\n"
    "---\n"
    "%2\n"
    "---\n\n"
    "Your previous translation:\n"
    "---\n"
    "%3\n"
    "---\n\n"
    "Return the corrected translation ONLY, with every placeholder ⟦PH…⟧ from "
    "the source present exactly once and markdown structure preserved.");

const QString REVIEW_SYSTEM = QStringLiteral(
    "You are a meticulous reviewer of scientific translations (%1 -> %2).\n"
    "Check the candidate translation for: mistranslations, omissions, additions, "
    "terminology errors, broken markdown, altered placeholder tokens (⟦PH…⟧).\n"
    "Respond with JSON only: {\"ok\": true} if the translation is good, or "
    "{\"ok\": false, \"revised\": \"<full corrected translation>\", \"notes\": \"<short list of fixes>\"}.\n"
    "Do not wrap the JSON in markdown fences.");

const QString REVIEW_USER = QStringLiteral(
    "Source:\n"
    "---\n"
    "%1\n"
    "---\n"
    "Candidate translation:\n"
    "---\n"
    "%2\n"
    "---");

QString buildTranslationSystem(const QString& sourceLang, const QString& targetLang,
                                const QString& context, const QStringList& glossaryHints) {
    QString system = QStringLiteral(
        "You are a professional translator of scientific papers from %1 to %2.\n\n"
        "STRICT RULES:\n"
        "1. Translate ONLY natural-language text. Return ONLY the translated markdown, "
        "no explanations, no preface, no code fences around the whole answer.\n"
        "2. Placeholder tokens like ⟦PH12⟧ stand for formulas, code, links and images. "
        "Copy every placeholder EXACTLY as-is, in the position where its content "
        "belongs. Never translate, alter, merge, drop or invent placeholders.\n"
        "3. Preserve markdown structure exactly: heading levels (#), lists, table "
        "layout (same number of rows and columns, | separators), bold/italic markers.\n"
        "4. Keep any remaining LaTeX untouched: commands, math, \\cite/\\ref keys.\n"
        "5. Do not translate: author names (transliterate only if standard), "
        "bibliographic entries' titles inside references, identifiers, dataset/model "
        "names, units of measurement.\n"
        "6. Use established %2 scientific terminology; keep terminology consistent "
        "across the document.")
        .arg(langName(sourceLang), langName(targetLang));

    if (!glossaryHints.isEmpty()) {
        system += QStringLiteral("\n\nTERMINOLOGY (use exactly these translations):\n%1")
                      .arg(glossaryHints.join('\n'));
    }
    if (!context.isEmpty()) {
        system += QStringLiteral("\n\nDOCUMENT CONTEXT (what this paper is about):\n%1")
                      .arg(context.left(1500));
    }
    return system;
}

QString buildUserMessage(const QString& maskedText, const QString& previousContext) {
    if (previousContext.isEmpty()) return maskedText;
    return QStringLiteral("PRECEDING TEXT (context only — do NOT translate or include it):\n"
                          "…%1\n\nTEXT TO TRANSLATE:\n%2")
        .arg(previousContext, maskedText);
}

} // namespace pdftransl
