# -*- coding: utf-8 -*-
"""PDF 转换模块：封装 pdf-inspector，实现分类 → 提取/OCR 的智能路由。

pdf-inspector 负责：
1. classify_pdf  : 快速判断 PDF 是文本型还是扫描型
2. process_pdf   : 文本型 PDF → 结构化 Markdown（含标题、表格、版式信息）
3. process_pdf_with_ocr : 扫描型 PDF → OCR 后转 Markdown
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdf_inspector as pi

# 修复 pdf-inspector 元数据标题的编码 bug：
# UTF-8 多字节字符（如 en-dash E2 80 93）会被拆成 替换字符(U+FFFD) + HTML实体 "&#x80;&#x93;"
_ENT_RE = re.compile(r"&#x([0-9a-fA-F]{2});|&#(\d{1,3});")
_BROKEN_SEQ_RE = re.compile(r"\ufffd(?:&#x[0-9a-fA-F]{2};|&#\d{1,3};)+")

# 常见 UTF-8 引导字节（按出现频率排序）
_LEAD_BYTES = (0xE2, 0xC3, 0xC2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9,
               0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF, 0xF0, 0xF1, 0xF2, 0xF3, 0xF4,
               0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xCB, 0xCC, 0xCD, 0xCE, 0xCF,
               0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xDB,
               0xDC, 0xDD, 0xDE, 0xDF, 0xE0, 0xE1, 0xF3, 0xF4)


def _decode_broken(match: re.Match) -> str:
    """把 '�&#x80;&#x93;' 之类的破损序列还原为 UTF-8 字符。"""
    raw = match.group(0)
    # 提取实体字节（十六进制或十进制）
    tail = bytearray()
    for hexv, decv in re.findall(r"&#x([0-9a-fA-F]{2});|&#(\d{1,3});", raw):
        if hexv:
            tail.append(int(hexv, 16))
        elif decv:
            tail.append(int(decv))
    if not tail:
        return raw
    tail = bytes(tail)
    # 尝试补引导字节解码：先试 tail 直接解码，再试补 1~3 字节引导
    candidates = [tail]
    candidates += [bytes([lead]) + tail for lead in _LEAD_BYTES]
    candidates += [bytes([lead1, lead2]) + tail for lead1 in (0xE0, 0xE1, 0xE2, 0xE3, 0xE4)
                   for lead2 in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
                                 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F,
                                 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97,
                                 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F)]
    for cand in candidates:
        try:
            decoded = cand.decode("utf-8")
            return decoded
        except UnicodeDecodeError:
            continue
    # 保底：实体还原为原始字符
    return tail.decode("latin-1", errors="replace")


def _fix_meta_encoding(text: str) -> str:
    """修复元数据标题中的破损 UTF-8 序列。"""
    if not text or "&#" not in text:
        return text
    return _BROKEN_SEQ_RE.sub(_decode_broken, text)


@dataclass
class PdfConversionResult:
    """一次 PDF 转换的完整结果。"""

    source: Path
    title: Optional[str]
    pdf_type: str                      # text_based | scanned | mixed
    page_count: int
    pages_needing_ocr: list            # 0-indexed 页号
    markdown: str
    ocr_reasons_by_page: dict = field(default_factory=dict)
    processing_time_ms: int = 0
    ocr_used: bool = False
    ocr_skipped: bool = False
    warnings: list = field(default_factory=list)
    has_encoding_issues: bool = False
    is_complex_layout: bool = False

    @property
    def needs_ocr(self) -> bool:
        return bool(self.pages_needing_ocr)


def classify_pdf(path: str | Path) -> pi.PdfClassification:
    """轻量分类：返回 pdf_type / page_count / pages_needing_ocr / confidence。"""
    return pi.classify_pdf(str(path))


def convert_pdf_to_markdown(
    path: str | Path,
    *,
    enable_ocr: bool = True,
    dpi: float = 150.0,
    model_directory: Optional[str] = None,
    offline: bool = False,
) -> PdfConversionResult:
    """将 PDF 转换为 Markdown，智能路由文本型/扫描型。

    策略：
    - 先 classify_pdf 快速分类；
    - 无扫描页 → process_pdf 直接提取（快、省）；
    - 有扫描页且启用 OCR → process_pdf_with_ocr（auto 模式按需初始化 OCR）；
    - 扫描页但禁用/失败 OCR → 降级为直接提取并记录 warning。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    start = time.perf_counter()

    # 1) 分类
    cls = pi.classify_pdf(str(path))
    pages_needing_ocr = list(cls.pages_needing_ocr or [])

    # 2) 路由：需要 OCR 的页面数
    if pages_needing_ocr and enable_ocr:
        try:
            kwargs = dict(
                mode="auto",
                dpi=dpi,
                offline=offline,
            )
            if model_directory:
                kwargs["model_directory"] = model_directory
            res = pi.process_pdf_with_ocr(str(path), **kwargs)
            ocr_used = True
        except Exception as exc:  # OCR 运行时/模型不可用 → 降级
            res = pi.process_pdf(str(path))
            ocr_used = False
            return _assemble(
                path, res, pages_needing_ocr, ocr_used, start,
                warnings=[f"OCR 不可用已降级为直接提取（{exc}）"],
            )
    else:
        # 3) 文本型：直接提取
        res = pi.process_pdf(str(path))
        ocr_used = False
    warnings = []
    ocr_skipped = bool(pages_needing_ocr and not enable_ocr)
    if ocr_skipped:
        warnings.append(
            "检测到需要 OCR 的页面，但用户通过 --no-ocr 主动跳过；提取结果可能不完整"
        )

    return _assemble(
        path,
        res,
        pages_needing_ocr,
        ocr_used,
        start,
        warnings=warnings,
        ocr_skipped=ocr_skipped,
    )


def _assemble(
    path: Path,
    res: pi.PdfResult,
    pages_needing_ocr: list,
    ocr_used: bool,
    start: float,
    warnings: Optional[list] = None,
    ocr_skipped: bool = False,
) -> PdfConversionResult:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    title = _fix_meta_encoding(res.title or "")
    markdown = _fix_meta_encoding(res.markdown or "")
    return PdfConversionResult(
        source=path,
        title=title or path.stem,
        pdf_type=res.pdf_type,
        page_count=res.page_count,
        pages_needing_ocr=pages_needing_ocr,
        markdown=markdown,
        ocr_reasons_by_page=dict(res.ocr_reasons_by_page or {}),
        processing_time_ms=elapsed_ms,
        ocr_used=ocr_used,
        ocr_skipped=ocr_skipped,
        warnings=warnings or [],
        has_encoding_issues=bool(getattr(res, "has_encoding_issues", False)),
        is_complex_layout=bool(getattr(res, "is_complex_layout", False)),
    )
