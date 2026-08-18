# -*- coding: utf-8 -*-
"""pdfreader - 基于 pdf-inspector 的 AI 文献阅读工具。

将外文 PDF 文献转换为结构化 Markdown，并通过任意 OpenAI 兼容 LLM
（默认 DeepSeek，可换其他模型）翻译为中文，输出中英对照报告，
补充图片提取与视觉理解，提升 AI 对文献的阅读效率。
"""

__version__ = "0.1.0"

from .convert import classify_pdf, convert_pdf_to_markdown, PdfConversionResult
from .chunk import chunk_markdown, estimate_tokens
from .translate import LLMTranslator, DeepSeekTranslator
from .report import build_bilingual_markdown, build_plain_chinese, build_html_report
from .figures import extract_figures, FigureExtractionResult, figures_to_markdown
from .vision import VisionReader, FigureReading, VisionResult, readings_to_markdown

__all__ = [
    "__version__",
    "classify_pdf",
    "convert_pdf_to_markdown",
    "PdfConversionResult",
    "chunk_markdown",
    "estimate_tokens",
    "LLMTranslator",
    "DeepSeekTranslator",
    "build_bilingual_markdown",
    "build_plain_chinese",
    "build_html_report",
    "extract_figures",
    "FigureExtractionResult",
    "figures_to_markdown",
    "VisionReader",
    "FigureReading",
    "VisionResult",
    "readings_to_markdown",
]
