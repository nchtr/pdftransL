from pathlib import Path
from agents.translator_agent import TranslatorAgent
from utils.file_utils import read_markdown, save_markdown

markdown_path = Path("data/output/final/article.md")
translated_path = Path("data/output/final/translated_article.md")
print("TRANSLATOR_AGENT_STARTED")

if translated_path.exists() and translated_path.stat().st_size > 1000:
    print("TRANSLATOR_STATUS:")
    print("Existing translated_article.md found. API translation skipped.")
    print("TRANSLATOR_INPUT:")
    print(markdown_path)
    print("TRANSLATOR_RESULT:")
    print(translated_path)

else:
    print("TRANSLATOR_STATUS:")
    print("No existing translation found. Starting API translation.")
    content = read_markdown(markdown_path)
    translator = TranslatorAgent()
    # Chunking, formula masking, validation and the repair loop are
    # handled inside the pdftransl engine now.
    final_translation = translator.translate(content)
    if not final_translation.strip():
        raise RuntimeError(
            "Translation failed: empty result. "
            "Check provider API keys and limits."
        )

    save_markdown(final_translation, translated_path)

    print("TRANSLATOR_STATUS:")
    print("Translation finished.")

    print("TRANSLATOR_RESULT:")
    print(translated_path)
