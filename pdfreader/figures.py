# -*- coding: utf-8 -*-
"""图片提取模块：从 PDF 中提取图片并关联图注。

pdf-inspector 只提取文本，图片会丢失。本模块用 PyMuPDF 补充图片提取：
1. 逐页提取内嵌图片（过滤过小的图标/噪声），保存为 PNG；
2. 从页面文本中定位图注（"Fig. N" / "Figure N"），按空间位置与图片匹配；
3. 输出结构化结果（页码、文件名、尺寸、图注），供报告引用。

用法（独立）：
    python -m pdfreader.figures <pdf路径> [输出目录]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import pymupdf as fitz  # PyMuPDF 新版推荐导入名
except ImportError:  # pragma: no cover
    import fitz  # 旧版兼容

MIN_SIZE = 60          # 小于该尺寸（宽或高）的图片视为噪声，过滤
CAPTION_PAT = re.compile(
    r"(?i)^\s*(fig(?:ure)?\.?|图)\s*(\d+)[\.:：]?\s*(.*)$"
)


@dataclass
class FigureInfo:
    """一张提取出的图片及其关联图注。"""

    page: int                 # 1-indexed 页码
    index: int                # 页内序号（1-indexed）
    path: Path                # 保存的 PNG 路径
    width: int
    height: int
    caption: str = ""         # 匹配到的图注文本
    caption_page: int = 0     # 图注所在页（可能跨页）
    matched: bool = False     # 是否成功匹配到图注

    @property
    def display_name(self) -> str:
        return f"Fig.{self.index}" if not self.caption else f"Fig.{self.caption.split()[0][-1] if False else ''}"


@dataclass
class FigureExtractionResult:
    """整篇 PDF 的图片提取结果。"""

    source: Path
    figures: list = field(default_factory=list)
    skipped_small: int = 0

    @property
    def count(self) -> int:
        return len(self.figures)


def _page_text_blocks(page) -> list[tuple[str, float, float]]:
    """返回页面文本块 [(文本, x0, y0)]，用于图注定位。"""
    blocks = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = b
        text = text.strip()
        if text:
            blocks.append((text, x0, y0))
    return blocks


def extract_figures(
    pdf_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    min_size: int = MIN_SIZE,
    render_dpi: int = 200,
) -> FigureExtractionResult:
    """提取 PDF 中的图片并匹配图注。

    方案：用 get_image_info 拿图片在页面上的 bbox（精确位置，用于图注匹配），
    再用 clip 按 bbox 渲染高分辨率像素保存——不依赖 xref 映射，鲁棒且清晰。

    Args:
        pdf_path: PDF 文件路径。
        out_dir: 图片保存目录（默认 <pdf同目录>/figures_<stem>）。
        min_size: 过滤小于该尺寸（像素）的图片。
        render_dpi: 渲染分辨率（默认 200，约 2.8x 显示尺寸）。
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    if out_dir is None:
        out_dir = pdf_path.parent / f"figures_{pdf_path.stem}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = FigureExtractionResult(source=pdf_path)
    doc = fitz.open(pdf_path)

    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            page_imgs = []
            for info in page.get_image_info():
                bbox = info["bbox"]
                w = round(bbox[2] - bbox[0])
                h = round(bbox[3] - bbox[1])
                if w < min_size or h < min_size:
                    result.skipped_small += 1
                    continue
                page_imgs.append({"bbox": bbox, "w": w, "h": h})

            if not page_imgs:
                continue

            # 该页图注（"Fig. N" 开头且行文本较短的块）
            captions = []
            for text, x0, y0 in _page_text_blocks(page):
                m = CAPTION_PAT.match(text)
                if m:
                    captions.append({"num": int(m.group(2)), "text": text.strip(), "y": y0})

            # 图注驱动匹配：图注通常位于图片正下方。
            # 按 y 排序后，每个图注匹配"位于其上方、最接近且未被占用"的图片。
            page_imgs.sort(key=lambda im: (im["bbox"][1], im["bbox"][0]))
            captions.sort(key=lambda c: c["y"])

            img_caption = {id(im): "" for im in page_imgs}
            used_imgs = set()
            for cap in captions:
                # 找该图注上方最接近、未占用的图片
                candidates = [
                    im for im in page_imgs
                    if id(im) not in used_imgs and im["bbox"][3] <= cap["y"] + 10
                ]
                if candidates:
                    best = max(candidates, key=lambda im: im["bbox"][3])  # 底部最接近图注
                    if abs(best["bbox"][3] - cap["y"]) < 300:
                        img_caption[id(best)] = cap["text"]
                        used_imgs.add(id(best))

            for i, im in enumerate(page_imgs, start=1):
                name = f"p{pno+1:02d}_fig{i:02d}_{im['w']}x{im['h']}.png"
                save_path = out_dir / name
                try:
                    clip = fitz.Rect(im["bbox"])
                    pix = page.get_pixmap(clip=clip, dpi=render_dpi)
                    pix.save(str(save_path))
                except Exception:  # noqa: BLE001
                    continue

                caption_text = img_caption.get(id(im), "")
                result.figures.append(
                    FigureInfo(
                        page=pno + 1,
                        index=i,
                        path=save_path,
                        width=im["w"],
                        height=im["h"],
                        caption=caption_text,
                        caption_page=pno + 1 if caption_text else 0,
                        matched=bool(caption_text),
                    )
                )
    finally:
        doc.close()

    return result


def figures_to_markdown(
    result: FigureExtractionResult,
    *,
    relative_to: str | Path | None = None,
) -> str:
    """把图片清单渲染成 Markdown，并生成相对报告目录的有效链接。"""
    if not result.figures:
        return ""
    from urllib.parse import quote

    base = Path(relative_to) if relative_to is not None else None
    lines = ["## 文献图片（已从 PDF 提取）", ""]
    for f in result.figures:
        if base is None:
            rel = f.path.name
        else:
            import os

            rel = Path(
                os.path.relpath(f.path.resolve(), base.resolve())
            ).as_posix()
        href = quote(rel, safe="/-._~")
        cap = f" — {f.caption}" if f.caption else ""
        lines.append(f"![{f.path.name}]({href}){cap}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：python -m pdfreader.figures <pdf> [out_dir]"""
    import argparse
    import io
    import sys

    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(prog="pdfreader.figures", description="从 PDF 提取图片并匹配图注")
    p.add_argument("pdf", help="PDF 文件路径")
    p.add_argument("-o", "--out-dir", default=None, help="图片输出目录")
    p.add_argument("--min-size", type=int, default=MIN_SIZE, help="过滤小于该尺寸的图片")
    args = p.parse_args(argv)

    res = extract_figures(args.pdf, args.out_dir, min_size=args.min_size)
    print(f"提取 {res.count} 张图片（跳过 {res.skipped_small} 张小图）到目录:")
    for f in res.figures:
        mark = " ✅" if f.matched else " ⚠️(未匹配图注)"
        cap = f" | {f.caption[:80]}" if f.caption else ""
        print(f"  p{f.page:02d} {f.path.name} ({f.width}x{f.height}){mark}{cap}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
