from pathlib import Path
from agents.translator_agent import TranslatorAgent
from utils.file_utils import read_markdown, save_markdown, split_text

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
    chunks = split_text(content)
    translator = TranslatorAgent()
    translated_chunks = []
    failed_chunks = []

    for i, chunk in enumerate(chunks):
        print(f"Translating chunk {i + 1}/{len(chunks)}")
        translated = translator.translate(chunk)
        if translated:
            translated_chunks.append(translated)
        else:
            failed_chunks.append(i + 1)
            print(f"Chunk {i + 1} failed")
    if not translated_chunks:
        raise RuntimeError(
            "Translation failed: no chunks were translated. "
            "Check OpenRouter limits."
        )

    final_translation = "\n\n".join(translated_chunks)

    save_markdown(final_translation, translated_path)

    print("TRANSLATOR_STATUS:")
    print(f"Translated chunks: {len(translated_chunks)}/{len(chunks)}")
    print(f"Failed chunks: {failed_chunks}")

    print("TRANSLATOR_RESULT:")
    print(translated_path)