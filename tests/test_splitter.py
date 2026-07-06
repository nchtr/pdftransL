from pdftransl.models import BlockType
from pdftransl.parsing.splitter import assemble, split_markdown

SAMPLE = """# Title

Intro paragraph with $x$ inline.

$$
E = mc^2
$$

| a | b |
|---|---|
| 1 | 2 |

![figure](images/f1.png)

```python
print("hi")
```

Final paragraph.
"""


def test_block_types():
    blocks = split_markdown(SAMPLE)
    types = [b.type for b in blocks]
    assert types == [
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.MATH,
        BlockType.TABLE,
        BlockType.IMAGE,
        BlockType.CODE,
        BlockType.PARAGRAPH,
    ]


def test_translatable_flags():
    blocks = split_markdown(SAMPLE)
    by_type = {b.type: b.translatable for b in blocks}
    assert by_type[BlockType.HEADING] is True
    assert by_type[BlockType.PARAGRAPH] is True
    assert by_type[BlockType.TABLE] is True
    assert by_type[BlockType.MATH] is False
    assert by_type[BlockType.CODE] is False
    assert by_type[BlockType.IMAGE] is False


def test_latex_env_block():
    md = "Text before.\n\n\\begin{equation}\na = b\n\\end{equation}\n\nText after."
    blocks = split_markdown(md)
    assert [b.type for b in blocks] == [
        BlockType.PARAGRAPH, BlockType.MATH, BlockType.PARAGRAPH,
    ]


def test_single_line_display_math():
    blocks = split_markdown("$$a=b$$")
    assert blocks[0].type == BlockType.MATH


def test_assemble_preserves_content():
    blocks = split_markdown(SAMPLE)
    out = assemble([b.text for b in blocks])
    assert "E = mc^2" in out
    assert "| a | b |" in out
    assert 'print("hi")' in out
