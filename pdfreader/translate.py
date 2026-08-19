# -*- coding: utf-8 -*-
"""LLM 翻译模块：调用任意 OpenAI 兼容的 LLM 对文献块进行中文学术翻译。

默认指向 DeepSeek，但 base_url / model / api_key 全部可配置，
任何 OpenAI 兼容服务（DeepSeek / OpenAI / Qwen / GLM / 中转站网关等）都能用。

设计要点：
- 分块串行翻译，保持文献阅读顺序；
- 系统提示词强调学术翻译规范（术语准确、保持结构、保留 Markdown）；
- 术语表机制：翻译过程中提取并复用专业术语，保证全文一致；
- 自动重试 + 指数退避，容忍 API 抖动；
- 原生 HTTP 实现，不依赖 openai SDK（兼容各类网关/中转服务）；
- 支持 DRY-RUN：无 API Key 时输出占位译文，便于离线验证流程。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .chunk import Chunk, estimate_tokens
from .http_retry import run_with_retries
from .url_security import validate_base_url

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

# API Key 环境变量候选（按优先级）
KEY_ENV_VARS = ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")

SYSTEM_PROMPT = """你是一位精通学术英语与简体中文的资深翻译专家，擅长计算机科学、工程领域的英文文献翻译。

翻译要求：
1. 忠实原文，术语准确；专业术语首次出现时给出中文翻译并保留英文原文，格式：中文（English Term）
2. 保留原文的 Markdown 结构（标题层级 #、列表、表格、代码块、加粗）与序号，不要增删章节
3. 公式、代码、引用 [1]、作者姓名、专有名词保持原文
4. 长难句拆分为符合中文习惯的短句，但不得改变原意
5. 只输出翻译结果，不要解释、不要添加译者注，不要输出与原文无关的内容
6. 若原文含标题（# 开头），标题行请翻译并保留 # 标记

输出格式：直接输出翻译后的 Markdown 全文。"""


@dataclass
class TranslationResult:
    """单个块的翻译结果。"""

    index: int
    source: str
    translation: str
    ok: bool = True
    error: str = ""
    terms: list = None  # 本块提取的术语 [(en, zh), ...]
    skipped: bool = False

    def __post_init__(self) -> None:
        if self.terms is None:
            self.terms = []


class LLMTranslator:
    """OpenAI 兼容 LLM 的分块翻译器（原生 HTTP 实现）。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 1.0,
        max_retries: int = 3,
        timeout: int = 180,
        allow_insecure_http: bool = False,
        glossary: Optional[dict] = None,
        on_progress: Optional[Callable[[int, int, Chunk], None]] = None,
        dry_run: bool = False,
        thinking: bool = False,
    ) -> None:
        self.api_key = api_key or self._resolve_key()
        self.model = model
        self.allow_insecure_http = allow_insecure_http
        self.base_url = validate_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
        )
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.glossary = dict(glossary or {})
        self.on_progress = on_progress
        self.dry_run = dry_run or not self.api_key
        self.thinking = thinking

    @staticmethod
    def _resolve_key() -> str:
        for var in KEY_ENV_VARS:
            val = os.environ.get(var, "")
            if val:
                return val
        return ""

    @property
    def available(self) -> bool:
        return not self.dry_run

    def _call(self, messages: list, max_tokens: int) -> str:
        """原生 HTTP 调用 /chat/completions，返回助手文本。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
        }
        # DeepSeek 深度思考开关：翻译默认关闭思考以节省 token，避免块因推理超长而失败。
        # 仅在 DeepSeek 端点下发 thinking 参数，避免污染其他 OpenAI 兼容服务。
        if "deepseek" in self.base_url.lower():
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
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
            raise RuntimeError(f"翻译接口调用失败: {exc}") from exc

    def test_connection(self) -> str:
        """发送最小文本请求，验证 URL、Key 和模型配置。"""
        if self.dry_run:
            raise RuntimeError("未配置翻译 API Key")
        return self._call(
            [
                {"role": "system", "content": "只回复 OK。"},
                {"role": "user", "content": "连接测试"},
            ],
            max_tokens=16,
        )

    def _build_user_prompt(self, chunk: Chunk) -> str:
        parts = [f"请将以下文献片段翻译为简体中文。\n"]
        if chunk.heading_path:
            parts.append(f"（所属章节：{chunk.heading_path}）\n")
        if self.glossary:
            gloss = "\n".join(f"- {en} → {zh}" for en, zh in self.glossary.items())
            parts.append(f"术语表（必须使用以下译法）：\n{gloss}\n")
        parts.append("\n原文如下：\n\n")
        parts.append(chunk.text)
        return "".join(parts)

    def _extract_terms(self, source: str, translation: str) -> list:
        """粗提取术语：译文中括号注释的 中文（English） 模式。"""
        terms: list = []
        pat = re.compile(r"([\u4e00-\u9fff]{2,12})[（(]([A-Za-z][A-Za-z0-9 .\-]{2,40})[)）]")
        for m in pat.finditer(translation):
            zh, en = m.group(1), m.group(2).strip()
            if en and zh and en.lower() not in {t.lower() for t, _ in terms}:
                terms.append((en, zh))
        return terms[:20]

    def translate_chunk(self, chunk: Chunk) -> TranslationResult:
        """翻译单个块，带重试。"""
        if self.dry_run:
            return TranslationResult(
                index=chunk.index,
                source=chunk.text,
                translation=self._dry_translation(chunk),
                terms=self._extract_terms(chunk.text, ""),
            )

        prompt = self._build_user_prompt(chunk)
        max_tokens = max(4096, int(estimate_tokens(chunk.text) * 3) + 2048)
        try:
            text = self._call(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
            )
            if not text:
                raise RuntimeError("空响应")
            terms = self._extract_terms(chunk.text, text)
            for en, zh in terms:
                self.glossary.setdefault(en, zh)
            return TranslationResult(
                index=chunk.index,
                source=chunk.text,
                translation=text,
                terms=terms,
            )
        except Exception as exc:  # noqa: BLE001
            return TranslationResult(
                index=chunk.index,
                source=chunk.text,
                translation="",
                ok=False,
                error=str(exc),
            )

    def translate_document(
        self,
        chunks: list[Chunk],
        *,
        skip_references: bool = True,
    ) -> list[TranslationResult]:
        """串行翻译整篇文献的所有块，返回有序结果。

        skip_references=True 时，参考文献块（chunk.is_reference）不调用 API，
        直接产出 skipped 结果，节省 token 与时间。
        """
        results: list[TranslationResult] = []
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            if self.on_progress:
                self.on_progress(i + 1, total, chunk)
            if skip_references and chunk.is_reference:
                results.append(TranslationResult(
                    index=chunk.index,
                    source=chunk.text,
                    translation="",
                    ok=False,
                    skipped=True,
                    error="参考文献，已跳过翻译",
                ))
            else:
                results.append(self.translate_chunk(chunk))
        return results

    def _dry_translation(self, chunk: Chunk) -> str:
        """无 API Key 时的占位译文（便于离线验证流程）。"""
        first_line = chunk.text.splitlines()[0] if chunk.text else ""
        key_hint = " / ".join(KEY_ENV_VARS)
        return (
            f"（DRY-RUN 占位译文 | 未配置 {key_hint}，实际翻译将在此输出）\n"
            f"原文首行：{first_line}\n"
            f"原文长度：{len(chunk.text)} 字符，估算 {estimate_tokens(chunk.text)} tokens\n"
            f"本块为第 {chunk.index + 1} 块。\n"
        )


# 向后兼容别名（旧名 DeepSeekTranslator 仍可用）
DeepSeekTranslator = LLMTranslator
