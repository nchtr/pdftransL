from pathlib import Path


def read_markdown(file_path):

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def save_markdown(content, file_path):

    file_path = Path(file_path)

    file_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def split_text(text, chunk_size=1200):

    chunks = []

    for i in range(0, len(text), chunk_size):

        chunk = text[i:i + chunk_size]

        chunks.append(chunk)

    return chunks