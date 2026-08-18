# -*- coding: utf-8 -*-
"""报告输出模块：生成中英对照 / 纯中文 / HTML 文献阅读报告。"""
from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .chunk import Chunk
from .convert import PdfConversionResult
from .translate import TranslationResult

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")


def _split_heading_off(text: str) -> tuple[str, str]:
    """把文本开头的标题行与正文拆开。"""
    lines = text.splitlines()
    if lines and _HEADING_RE.match(lines[0]):
        return lines[0], "\n".join(lines[1:]).strip()
    return "", text


def _normalize_heading_markdown(text: str) -> str:
    """确保文本中 Markdown 标题行以空行分隔，便于渲染。"""
    lines = text.splitlines()
    out: list[str] = []
    for ln in lines:
        if _HEADING_RE.match(ln) and out:
            out.append("")
        out.append(ln)
    return "\n".join(out)


def build_bilingual_markdown(
    title: str,
    chunks: list[Chunk],
    results: list[TranslationResult],
) -> str:
    """构建中英对照 Markdown：逐块『原文 + 译文』交替展示。

    结构：
        # 标题（中英）
        > 元信息
        ---
        ## 块 1
        ### 原文
        ...
        ### 译文
        ...
    """
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("> 本文档由 pdfreader 生成：pdf-inspector 提取 + LLM 翻译。")
    lines.append("> 中英对照模式：每块先原文后译文。")
    lines.append("")

    failed = [r for r in results if not r.ok and not r.skipped]
    if failed:
        lines.append(f"> ⚠️ 有 {len(failed)} 块翻译失败，见文末错误清单。")
        lines.append("")

    for chunk, res in zip(chunks, results):
        lines.append("---")
        lines.append("")
        heading, body = _split_heading_off(chunk.text)
        heading_text = heading.lstrip("#").strip() if heading else f"段落 {chunk.index + 1}"
        lines.append(f"## 块 {chunk.index + 1} · {heading_text}")
        lines.append("")
        if body:
            lines.append("### 原文")
            lines.append("")
            lines.append(_normalize_heading_markdown(body))
            lines.append("")
        lines.append("### 译文")
        lines.append("")
        if res.ok:
            lines.append(_normalize_heading_markdown(res.translation))
        elif res.skipped:
            lines.append("> 已跳过翻译；请阅读上方原文或使用 extracted.md。")
        else:
            lines.append(f"> ⚠️ 翻译失败：{res.error}")
        lines.append("")

    if failed:
        lines.append("---")
        lines.append("## 翻译失败清单")
        lines.append("")
        for r in failed:
            lines.append(f"- 块 {r.index + 1}: {r.error}")
        lines.append("")

    return "\n".join(lines)


def build_plain_chinese(
    title: str,
    chunks: list[Chunk],
    results: list[TranslationResult],
) -> str:
    """构建纯中文译文 Markdown（去掉原文，保留结构与标题）。"""
    lines: list[str] = []
    lines.append(f"# {title}（中文译文）")
    lines.append("")

    for chunk, res in zip(chunks, results):
        heading, body = _split_heading_off(chunk.text)
        # 译文里若带标题则直接使用
        if res.ok:
            trans_heading, trans_body = _split_heading_off(res.translation)
            if trans_heading:
                lines.append(trans_heading)
            if trans_body:
                lines.append("")
                lines.append(_normalize_heading_markdown(trans_body))
        elif res.skipped:
            lines.append(f"> 块 {chunk.index + 1} 已跳过翻译。")
        else:
            lines.append(f"> ⚠️ 块 {chunk.index + 1} 翻译失败：{res.error}")
        lines.append("")

    return "\n".join(lines)


def _md_to_html(text: str) -> str:
    """极简 Markdown → HTML（标题/加粗/斜体/代码/段落）。"""
    out: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{html.escape(m.group(2).strip())}</h{level}>")
            continue
        if line.strip() == "---":
            out.append("<hr>")
            continue
        if not line.strip():
            continue
        esc = html.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"\*(.+?)\*", r"<em>\1</em>", esc)
        esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
        if esc.strip().startswith("> "):
            out.append(f"<blockquote>{esc.strip()[2:]}</blockquote>")
        elif esc.strip().startswith(("- ", "* ")):
            out.append(f"<li>{esc.strip()[2:]}</li>")
        else:
            out.append(f"<p>{esc}</p>")
    return "\n".join(out)


def build_html_report(
    title: str,
    chunks: list[Chunk],
    results: list[TranslationResult],
) -> str:
    """构建单文件 HTML 报告（中英对照，可离线打开）。"""
    body: list[str] = []
    body.append(f"<h1>{html.escape(title)}</h1>")
    body.append("<p class='meta'>由 pdfreader 生成 · 中英对照阅读报告</p>")

    for chunk, res in zip(chunks, results):
        heading, body_txt = _split_heading_off(chunk.text)
        heading_text = heading.lstrip("#").strip() if heading else f"段落 {chunk.index + 1}"
        body.append(f"<h2>块 {chunk.index + 1} · {html.escape(heading_text)}</h2>")
        if body_txt:
            body.append("<div class='side original'><h3>原文</h3>")
            body.append(_md_to_html(body_txt))
            body.append("</div>")
        body.append("<div class='side translation'><h3>译文</h3>")
        if res.ok:
            body.append(_md_to_html(res.translation))
        elif res.skipped:
            body.append("<p class='meta'>已跳过翻译；请阅读原文。</p>")
        else:
            body.append(f"<p class='err'>⚠️ 翻译失败：{html.escape(res.error)}</p>")
        body.append("</div>")

    css = """
    body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; max-width: 1000px;
           margin: 0 auto; padding: 24px; line-height: 1.7; color: #222; }
    h1 { border-bottom: 3px solid #2f6f4f; padding-bottom: 8px; }
    h2 { margin-top: 36px; background: #eef5f0; padding: 8px 12px; border-left: 4px solid #2f6f4f; }
    .meta { color: #888; font-size: 13px; }
    .side { padding: 8px 16px; margin: 8px 0; }
    .original { background: #fafafa; border-left: 3px solid #999; }
    .translation { background: #f3f8f4; border-left: 3px solid #2f6f4f; }
    .err { color: #c0392b; }
    code { background: #eee; padding: 1px 4px; border-radius: 3px; }
    table { border-collapse: collapse; } td, th { border: 1px solid #ccc; padding: 4px 8px; }
    blockquote { color: #555; border-left: 3px solid #ccc; margin: 8px 0; padding-left: 12px; }
    """
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
{chr(10).join(body)}
</body>
</html>"""


def write_report(
    result: PdfConversionResult,
    chunks: list[Chunk],
    translations: list[TranslationResult],
    out_dir: str | Path,
    *,
    formats: tuple[str, ...] = ("md",),
) -> dict[str, Path]:
    """写出报告文件，返回 {格式: 路径}。支持 md（中英对照）、zh（纯中文）、html。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = result.source.stem
    written: dict[str, Path] = {}

    if "md" in formats:
        p = out_dir / f"{stem}.bilingual.md"
        p.write_text(
            build_bilingual_markdown(result.title or stem, chunks, translations),
            encoding="utf-8",
        )
        written["md"] = p
    if "zh" in formats:
        p = out_dir / f"{stem}.zh.md"
        p.write_text(
            build_plain_chinese(result.title or stem, chunks, translations),
            encoding="utf-8",
        )
        written["zh"] = p
    if "html" in formats:
        p = out_dir / f"{stem}.report.html"
        p.write_text(
            build_html_report(result.title or stem, chunks, translations),
            encoding="utf-8",
        )
        written["html"] = p

    # 保存原始提取的 Markdown，供后续喂给 AI 或复译
    raw = out_dir / f"{stem}.extracted.md"
    raw.write_text(result.markdown, encoding="utf-8")
    written["extracted"] = raw

    return written


def write_figures_report(
    figures_result,
    out_dir: str | Path,
    *,
    formats: tuple[str, ...] = ("md",),
) -> dict[str, Path]:
    """把提取的图片清单写成 Markdown（含本地图片引用），便于对照阅读。"""
    from .figures import figures_to_markdown

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = figures_result.source.stem
    written: dict[str, Path] = {}

    md = figures_to_markdown(figures_result, relative_to=out_dir)
    if md:
        p = out_dir / f"{stem}.figures.md"
        p.write_text(md, encoding="utf-8")
        written["figures"] = p
    return written
