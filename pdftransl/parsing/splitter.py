"""Split Markdown into typed structural blocks.

The splitter is intentionally conservative: anything it cannot
classify stays a paragraph (translatable) and formula protection is
handled later by the masking layer, so misclassification never
corrupts math.
"""

from __future__ import annotations

import re

from pdftransl.models import Block, BlockType, TRANSLATABLE_TYPES

_HEADING_RE = re.compile(r"^#{1,6}\s")
_IMAGE_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_DISPLAY_MATH_LINE_RE = re.compile(r"^\s*\$\$")
_HTML_BLOCK_RE = re.compile(r"^\s*</?(?:div|table|tr|td|th|img|figure|p|span|br|hr)\b", re.I)
_BEGIN_ENV_RE = re.compile(r"^\s*\\begin\{([a-zA-Z*]+)\}")


def _make_block(btype: BlockType, lines: list[str], index: int) -> Block:
    return Block(
        type=btype,
        text="\n".join(lines).rstrip("\n"),
        index=index,
        translatable=btype in TRANSLATABLE_TYPES,
    )


def split_markdown(markdown: str) -> list[Block]:
    """Split a Markdown document into an ordered list of blocks."""
    lines = markdown.splitlines()
    blocks: list[Block] = []
    buf: list[str] = []
    buf_type: BlockType | None = None
    fence_marker: str | None = None
    math_open = False
    env_name: str | None = None

    def flush(btype: BlockType | None = None) -> None:
        nonlocal buf, buf_type
        if buf:
            blocks.append(_make_block(btype or buf_type or BlockType.PARAGRAPH, buf, len(blocks)))
        buf = []
        buf_type = None

    for line in lines:
        # --- multi-line states -------------------------------------
        if fence_marker is not None:
            buf.append(line)
            if line.strip().startswith(fence_marker):
                fence_marker = None
                flush(BlockType.CODE)
            continue
        if math_open:
            buf.append(line)
            # closing $$ (line containing the second delimiter)
            if "$$" in line:
                math_open = False
                flush(BlockType.MATH)
            continue
        if env_name is not None:
            buf.append(line)
            if re.search(r"\\end\{" + re.escape(env_name) + r"\}", line):
                env_name = None
                flush(BlockType.MATH)
            continue

        stripped = line.strip()

        # --- state openers ------------------------------------------
        m = _FENCE_RE.match(line)
        if m:
            flush()
            buf.append(line)
            fence_marker = m.group(1)
            continue
        if _DISPLAY_MATH_LINE_RE.match(line):
            flush()
            buf.append(line)
            # single-line $$...$$ ?
            if stripped.count("$$") >= 2:
                flush(BlockType.MATH)
            else:
                math_open = True
            continue
        m = _BEGIN_ENV_RE.match(line)
        if m:
            flush()
            buf.append(line)
            name = m.group(1)
            if re.search(r"\\end\{" + re.escape(name) + r"\}", line):
                flush(BlockType.MATH)
            else:
                env_name = name
            continue

        # --- single-line / grouped constructs ------------------------
        if not stripped:
            flush()
            continue
        if _HEADING_RE.match(line):
            flush()
            buf.append(line)
            flush(BlockType.HEADING)
            continue
        if _IMAGE_ONLY_RE.match(line):
            flush()
            buf.append(line)
            flush(BlockType.IMAGE)
            continue
        if _TABLE_ROW_RE.match(line):
            if buf_type != BlockType.TABLE:
                flush()
                buf_type = BlockType.TABLE
            buf.append(line)
            continue
        if _HTML_BLOCK_RE.match(line) and buf_type is None and not buf:
            buf.append(line)
            flush(BlockType.HTML)
            continue

        # default: paragraph text
        if buf_type == BlockType.TABLE:
            flush()
        if buf_type is None:
            buf_type = BlockType.PARAGRAPH
        buf.append(line)

    flush()
    return blocks


def assemble(blocks_text: list[str]) -> str:
    """Join block texts back into a Markdown document."""
    return "\n\n".join(t for t in blocks_text if t.strip() != "") + "\n"


_REFERENCES_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:\d+\.?\s*)?(references|bibliography|список литературы|литература|"
    r"cited works|works cited)\s*$",
    re.IGNORECASE,
)
# Headings that end the references section (appendices etc.)
_POST_REFERENCES_RE = re.compile(
    r"^#{1,6}\s*(?:[A-Z]?\d*\.?\s*)?(appendix|supplementary|acknowledg|приложение)",
    re.IGNORECASE,
)


def mark_references(blocks: list[Block]) -> int:
    """Mark the bibliography section as non-translatable.

    Reference entries (authors, titles, venues) should stay in the
    original language — translating them breaks citability. Returns
    the number of blocks marked.
    """
    marked = 0
    in_refs = False
    for block in blocks:
        if block.type == BlockType.HEADING:
            if _REFERENCES_HEADING_RE.match(block.text.strip()):
                in_refs = True
                continue  # the heading itself stays translatable
            if in_refs and _POST_REFERENCES_RE.match(block.text.strip()):
                in_refs = False
        elif in_refs and block.translatable:
            block.translatable = False
            block.meta["skipped"] = "references"
            marked += 1
    return marked
