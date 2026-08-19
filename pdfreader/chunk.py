# -*- coding: utf-8 -*-
"""文本分块模块：将长文献的 Markdown 按标题/段落切分为适合 LLM 的块。

策略：
- 优先按 Markdown 标题（# ~ ####）切分，保持结构语义；
- 标题下内容过大时，按空行切段落，再按字符上限合并；
- 相邻块保留少量重叠，避免断句破坏上下文；
- 输出带块序号，便于翻译后按序重组。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 粗略估算：英文约 4 字符/token，中文约 1.6 字符/token
_CHARS_PER_TOKEN = 4.0

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_CODE_FENCE_RE = re.compile(r"^```")
_TABLE_ROW_RE = re.compile(r"^\s*\|")

# 参考文献类标题（小写），用于「跳过参考文献翻译」。中英文都覆盖。
REFERENCE_HEADINGS = (
    "references",
    "bibliography",
    "references and notes",
    "notes and references",
    "literature cited",
    "works cited",
    "reference list",
    "sources",
    "参考文献",
    "引用文献",
)


def _is_reference_heading(heading: str) -> bool:
    """判断标题是否为参考文献类标题（大小写不敏感，忽略编号与首尾标点）。"""
    h = re.sub(r"^[\d.]+[\s.]*", "", heading.strip()).strip().strip(":.").lower()
    return h in REFERENCE_HEADINGS


# 参考文献标题行（整行匹配）：支持 `# References`、`**References**`、`References` 等形式。
_REFERENCE_MARKER_LINE_RE = re.compile(
    r"^\s*(?:\d+[.\s]+)?(?:#{1,4}\s+)?\*{0,2}\s*"
    r"(?:references|bibliography|references and notes|notes and references|"
    r"literature cited|works cited|reference list|sources|参考文献|引用文献)"
    r"\*{0,2}\s*:?\s*$",
    re.IGNORECASE,
)


def _contains_reference_marker(text: str) -> bool:
    """判断文本中是否存在参考文献标题行（# 标题 / **加粗** / 纯文本行）。"""
    return any(_REFERENCE_MARKER_LINE_RE.match(line) for line in text.splitlines())


@dataclass
class Chunk:
    """一个待翻译/待处理的文本块。"""

    index: int
    text: str
    heading_path: str = ""   # 所属标题路径，如 "1. Introduction > 1.1 Background"
    char_count: int = 0
    is_reference: bool = False  # 是否为参考文献部分

    def __post_init__(self) -> None:
        self.char_count = len(self.text)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（英文为主；中文按字符折算）。"""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def _has_real_content(text: str) -> bool:
    """判断文本去掉标题行后是否仍有正文内容。"""
    lines = [ln for ln in text.splitlines() if ln.strip() and not _HEADING_RE.match(ln)]
    return bool(lines)


def _split_by_headings(markdown: str) -> list[tuple[str, str]]:
    """按标题切分，返回 [(标题路径, 含标题行的正文)]。

    标题行保留在块文本中（供展示），heading_path 记录最近标题用于翻译提示；
    只有标题没有正文的"空章节"会被并入下一个有内容的块，避免产生空块。
    """
    sections: list[tuple[str, str]] = []
    buf: list[str] = []
    cur_heading = ""

    def flush() -> None:
        nonlocal buf
        text = "\n".join(buf).strip()
        if text and _has_real_content(text):
            sections.append((cur_heading, text))
        buf = []

    for line in markdown.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            # 若当前 buf 只有标题行（无正文），说明是连续的标题行（PDF 标题跨行），合并
            if buf and not _has_real_content("\n".join(buf)):
                cur_heading = (cur_heading + " " + m.group(2).strip()).strip()
                buf.append(line)
                continue
            flush()
            cur_heading = m.group(2).strip()
            buf.append(line)
        else:
            buf.append(line)
    flush()

    if not sections:
        sections = [("", markdown.strip())]
    return sections


def _hard_split(text: str, max_chars: int) -> list[str]:
    """兜底切分超长单行/段落，确保任何片段都不超过 max_chars。"""
    if len(text) <= max_chars:
        return [text]
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _split_body_into_blocks(body: str, max_chars: int) -> list[str]:
    """把某标题下的内容按段落/代码块/表格切成不超过 max_chars 的块。"""
    if len(body) <= max_chars:
        return [body]

    blocks: list[str] = []
    current: list[str] = []
    current_len = 0
    in_code = False

    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line.strip()):
            in_code = not in_code
            current.append(line)
            current_len += len(line) + 1
            continue

        # 段落分隔：空行
        if not line.strip() and current:
            blocks.append("\n".join(current))
            current, current_len = [], 0
            continue

        line_len = len(line) + 1
        if line_len > max_chars:
            if current:
                blocks.append("\n".join(current))
                current, current_len = [], 0
            blocks.extend(_hard_split(line, max_chars))
            continue
        if current_len + line_len > max_chars and current:
            blocks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += line_len

    if current:
        blocks.append("\n".join(current))

    # 合并过小的块（< 60% max），减少请求数
    merged: list[str] = []
    for b in blocks:
        if merged and len(merged[-1]) + len(b) <= max_chars:
            merged[-1] = merged[-1] + "\n" + b
        else:
            merged.append(b)
    return merged


def chunk_markdown(
    markdown: str,
    *,
    max_tokens: int = 3500,
    overlap_chars: int = 200,
) -> list[Chunk]:
    """将 Markdown 切分为 Chunk 列表。

    Args:
        markdown: pdf-inspector 输出的 Markdown。
        max_tokens: 每块最大 token 数（按字符估算）。
        overlap_chars: 相邻块重叠字符数，保持上下文。
    """
    max_chars = max(800, int(max_tokens * _CHARS_PER_TOKEN))
    sections = _split_by_headings(markdown)

    chunks: list[Chunk] = []
    idx = 0
    for heading_path, body in sections:
        blocks = _split_body_into_blocks(body, max_chars)
        for b in blocks:
            # 加上重叠
            text = b
            if overlap_chars > 0 and idx > 0:
                prev_tail = chunks[-1].text[-overlap_chars:]
                text = prev_tail + "\n\n" + b if not b.startswith("#") else b
            chunks.append(Chunk(index=idx, text=text, heading_path=heading_path))
            idx += 1

    # 合并过小的碎片块（页眉/页脚噪声、短段落），减少 API 调用次数。
    # 只合并"正文行数很少"的块：无真实正文内容（如期刊页眉）或短小段落。
    MIN_CHUNK_CHARS = 600
    merged: list[Chunk] = []
    for c in chunks:
        first = c.text.splitlines()[0] if c.text else ""
        is_fragment = (
            len(c.text) < MIN_CHUNK_CHARS
            and not _has_real_content(c.text)  # 纯标题/页眉噪声
        ) or (
            len(c.text) < 200  # 或极短的段落
        )
        if is_fragment and merged and len(merged[-1].text) + len(c.text) <= max_chars:
            merged[-1].text = merged[-1].text + "\n\n" + c.text
            merged[-1].char_count = len(merged[-1].text)
            if not merged[-1].heading_path and c.heading_path:
                merged[-1].heading_path = c.heading_path
        else:
            merged.append(c)
    chunks = merged
    # 重编号
    for i, c in enumerate(chunks):
        c.index = i

    # 定位参考文献：从第一个含参考文献标题行的块起，其后全部标记为 reference。
    # 标题形式支持 `# References`、`**References**`、纯文本 `References` 等。
    ref_start: int | None = None
    for i, c in enumerate(chunks):
        if _is_reference_heading(c.heading_path) or _contains_reference_marker(c.text):
            ref_start = i
            break
    if ref_start is not None:
        for c in chunks[ref_start:]:
            c.is_reference = True

    # 空文档保护
    if not chunks:
        chunks.append(Chunk(index=0, text=markdown or "", heading_path=""))
    return chunks
