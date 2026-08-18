# -*- coding: utf-8 -*-
"""图片理解模块：调用 OpenAI 兼容的视觉模型（VLM）解读论文图表。

背景：pdf-inspector 只提取文本，图片需另走视觉模型理解。
本模块通过标准 OpenAI 兼容接口（base_url 可配置）发送图片 + 图注 + 上下文，
让 VLM 生成结构化图表解读（图表类型、元素、数据趋势、与论文的关系）。

设计要点：
- 用标准 HTTP（urllib）实现，避免 openai SDK 与中转站网关不兼容的问题；
- base_url / model / api_key 全部可配置，兼容 GPT-4o、Qwen-VL、GLM-4V 等；
- 输入：图片路径 + 图注 + 相邻章节文本（可选）；
- 输出：结构化的中文图表解读（JSON 风格分段文本）；
- 失败自动重试 + DRY-RUN 降级。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .http_retry import run_with_retries
from .url_security import validate_base_url

DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

FIGURE_PROMPT = """你是一位学术论文图表分析专家。请分析这张论文插图，用中文输出结构化解读，包含：

1. 图表类型：流程图 / 架构图 / 折线图 / 柱状图 / 散点图 / 表格 / 示意图 / 截图等
2. 内容概述：图里展示了什么（2-3 句）
3. 关键元素：图中的主要组件、标注、坐标轴含义（如有）
4. 信息要点：该图传达的核心信息或数据趋势（如有）
5. 与论文的关系：结合图注，说明该图支撑了论文的什么论点

图注：{caption}
上下文（可选）：{context}

请用简洁的中文分条输出，不要超过 300 字。"""


@dataclass
class FigureReading:
    """一张图的视觉解读结果。"""

    figure_path: Path
    caption: str
    reading: str
    ok: bool = True
    error: str = ""
    model: str = ""
    latency_ms: int = 0


@dataclass
class VisionResult:
    """整篇文献的图片解读结果。"""

    readings: list = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.readings if r.ok)

    @property
    def total(self) -> int:
        return len(self.readings)


class VisionReader:
    """OpenAI 兼容视觉模型封装（原生 HTTP 实现）。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 3,
        timeout: int = 120,
        allow_insecure_http: bool = False,
        on_progress: Optional[Callable[[int, int, FigureReading], None]] = None,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get(
            "VISION_API_KEY", os.environ.get("OPENAI_API_KEY", "")
        )
        self.model = model
        self.allow_insecure_http = allow_insecure_http
        self.base_url = validate_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
        )
        self.max_retries = max_retries
        self.timeout = timeout
        self.on_progress = on_progress
        self.dry_run = dry_run or not self.api_key

    @property
    def available(self) -> bool:
        return not self.dry_run

    def _call(self, messages: list, max_tokens: int = 600) -> str:
        """原生 HTTP 调用 chat/completions，返回助手文本。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        def request_once() -> str:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:  # noqa: BLE001
                    detail = ""
                if detail:
                    exc.msg = f"{exc.msg} | {detail}"
                raise
            if not isinstance(data, dict):
                raise RuntimeError(f"非标准响应: {str(data)[:200]}")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("响应缺少 choices")
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise RuntimeError("响应 message.content 不是文本")
            return content.strip()

        try:
            return run_with_retries(request_once, max_attempts=self.max_retries)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"视觉模型调用失败: {exc}") from exc

    def _encode_image(self, image_path: Path) -> str:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        ext = image_path.suffix.lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(ext, "png")
        return f"data:image/{mime};base64,{b64}"

    def read_figure(
        self,
        image_path: str | Path,
        caption: str = "",
        context: str = "",
    ) -> FigureReading:
        """解读单张图片。"""
        image_path = Path(image_path)
        if not image_path.exists():
            return FigureReading(
                figure_path=image_path, caption=caption, reading="",
                ok=False, error="图片文件不存在",
            )

        if self.dry_run:
            return FigureReading(
                figure_path=image_path, caption=caption,
                reading=self._dry_reading(caption),
                model=self.model,
            )

        prompt = FIGURE_PROMPT.format(caption=caption or "（无图注）", context=context[:500] or "（无）")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": self._encode_image(image_path)}},
                ],
            }
        ]
        start = time.perf_counter()
        try:
            text = self._call(messages)
            return FigureReading(
                figure_path=image_path,
                caption=caption,
                reading=text,
                model=self.model,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return FigureReading(
                figure_path=image_path,
                caption=caption,
                reading="",
                ok=False,
                error=str(exc),
                latency_ms=int((time.perf_counter() - start) * 1000),
            )

    def read_figures(
        self,
        figures,
        context_by_page: Optional[dict] = None,
    ) -> VisionResult:
        """批量解读提取出的图片。

        Args:
            figures: FigureExtractionResult.figures 或类似对象列表（需有 path/caption/page）。
            context_by_page: {页码: 该页文本摘录}，作为上下文提示。
        """
        result = VisionResult()
        total = len(figures)
        for i, f in enumerate(figures):
            ctx = ""
            if context_by_page and f.page in context_by_page:
                ctx = context_by_page[f.page][:500]
            reading = self.read_figure(f.path, f.caption, ctx)
            result.readings.append(reading)
            if self.on_progress:
                self.on_progress(i + 1, total, reading)
        return result

    def _dry_reading(self, caption: str) -> str:
        return (
            f"（DRY-RUN 占位解读 | 未配置 VISION_API_KEY）\n"
            f"图注：{caption or '（无）'}\n"
            f"真实解读将由视觉模型生成。"
        )


def readings_to_markdown(readings: list[FigureReading]) -> str:
    """把图片解读渲染成 Markdown。"""
    if not readings:
        return ""
    lines = ["## 图表解读（视觉模型生成）", ""]
    for r in readings:
        lines.append(f"### {r.figure_path.name}")
        lines.append("")
        if r.caption:
            lines.append(f"**图注：** {r.caption}")
            lines.append("")
        if r.ok:
            lines.append(r.reading.strip())
        else:
            lines.append(f"> ⚠️ 解读失败：{r.error}")
        lines.append("")
    return "\n".join(lines)
