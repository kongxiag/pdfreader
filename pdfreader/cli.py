# -*- coding: utf-8 -*-
"""命令行入口：python -m pdfreader <pdf路径> [选项]

示例：
    python -m pdfreader paper.pdf                    # 转换 + 翻译，输出中英对照 md
    python -m pdfreader paper.pdf --formats md,html  # 同时输出 HTML 报告
    python -m pdfreader *.pdf --out-dir out/         # 批量处理
    python -m pdfreader paper.pdf --no-translate     # 只转换提取，不调用 API
    python -m pdfreader paper.pdf --chunk-tokens 2000  # 调整分块大小
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import re
import sys
from pathlib import Path

# Windows 控制台 UTF-8
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from . import __version__
from .chunk import chunk_markdown
from .convert import convert_pdf_to_markdown
from .report import write_report, write_figures_report
from .translate import LLMTranslator
from .url_security import validate_base_url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdfreader",
        description="基于 pdf-inspector 的 AI 文献阅读工具："
                    "PDF → Markdown → 中文翻译（任意 OpenAI 兼容 LLM）→ 中英对照报告",
    )
    p.add_argument("pdfs", nargs="*", help="PDF 文件路径（支持多个/通配符）")
    p.add_argument("--out-dir", "-o", default="output", help="输出目录（默认 output/）")
    p.add_argument(
        "--formats", default="md",
        help="输出格式，逗号分隔：md(中英对照), zh(纯中文), html(网页)（默认 md）",
    )
    p.add_argument("--no-translate", action="store_true", help="只转换提取，不调用翻译 API")
    p.add_argument("--no-ocr", action="store_true", help="禁用扫描件 OCR（有扫描页时降级直接提取）")
    p.add_argument("--figures", action="store_true", default=True,
                   help="提取 PDF 内嵌图片并匹配图注（默认开启，--no-figures 关闭）")
    p.add_argument("--no-figures", dest="figures", action="store_false", help="关闭图片提取")
    p.add_argument("--fig-min-size", type=int, default=80, help="图片提取过滤尺寸（默认 80px）")
    p.add_argument("--vision", action="store_true", default=False,
                   help="用视觉模型（VLM）解读图片（需配置视觉 Key，默认关闭）")
    p.add_argument("--no-vision", action="store_true", default=False,
                   help="显式禁用视觉处理，可覆盖配置文件中的 use_vision=true")
    p.add_argument("--vision-only", action="store_true", default=False,
                   help="只提取图片并执行视觉解读，不转换或翻译正文")
    p.add_argument("--vision-model", default=None, help="视觉模型名（默认 gpt-5.4-mini）")
    p.add_argument("--vision-base-url", default=None,
                   help="视觉模型接口地址（OpenAI 兼容），中转站请填其 /v1 地址")
    p.add_argument("--vision-api-key", default=None,
                   help="视觉模型 API Key（默认读环境变量 VISION_API_KEY / OPENAI_API_KEY）")
    p.add_argument("--chunk-tokens", type=int, default=3500, help="每块最大 token（默认 3500）")
    p.add_argument("--model", default=None,
                   help="翻译模型名（默认 deepseek-chat，可换任意 OpenAI 兼容模型，如 gpt-4o / qwen-max）")
    p.add_argument("--base-url", default=None,
                   help="翻译模型接口地址（OpenAI 兼容，默认 DeepSeek；其他服务/中转站填其 /v1 地址）")
    p.add_argument("--api-key", default=None,
                   help="翻译 API Key（默认读环境变量 LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）")
    p.add_argument("--config", default=None,
                   help="JSON 配置文件路径（skill 生成），提供 translate/vision 的默认参数；命令行显式参数优先")
    p.add_argument("--allow-insecure-http", action="store_true", default=False,
                   help="显式允许远程 HTTP API（不加密，会暴露 Key 和文献内容；默认拒绝）")
    p.add_argument("--test-config", action="store_true", default=False,
                   help="发送最小请求测试翻译/视觉 API 配置，不处理 PDF")
    p.add_argument("--json-out", default=None,
                   help="将机器可读结果(JSON)写入指定文件，供 DSH 插件等程序化调用解析")
    p.add_argument("--temperature", type=float, default=1.0, help="翻译温度（默认 1.0）")
    p.add_argument("--version", action="version", version=f"pdfreader {__version__}")
    return p


def _progress(done: int, total: int, chunk) -> None:
    print(f"  [翻译进度] {done}/{total} 块（{chunk.heading_path or f'段落 {chunk.index + 1}'}）", flush=True)


def _safe_output_name(stem: str, source_path: str | Path | None = None) -> str:
    """生成适合目录名的文献标识；可附加路径哈希避免同名 PDF 冲突。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" ._")
    name = name[:100] or "document"
    if source_path is not None:
        resolved = str(Path(source_path).resolve()).lower().encode("utf-8")
        name = f"{name}-{hashlib.sha256(resolved).hexdigest()[:8]}"
    return name


def _load_config(config_path: str | None) -> dict:
    """读取并验证 JSON 配置。显式配置错误必须失败，不能静默降级。"""
    if not config_path:
        return {}
    import json as _json

    path = Path(config_path)
    if not path.is_file():
        raise ValueError(f"配置文件不存在: {config_path}")
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件解析失败: {config_path} ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")
    _validate_config(data)
    return data


def _validate_config(config: dict) -> None:
    allowed_root = {"translate", "vision", "use_vision", "allow_insecure_http"}
    unknown = set(config) - allowed_root
    if unknown:
        raise ValueError(f"配置文件包含未知字段: {', '.join(sorted(unknown))}")

    for section_name in ("translate", "vision"):
        section = config.get(section_name, {})
        if not isinstance(section, dict):
            raise ValueError(f"配置字段 {section_name} 必须是对象")
        unknown_fields = set(section) - {"model", "base_url", "api_key"}
        if unknown_fields:
            raise ValueError(
                f"配置字段 {section_name} 包含未知项: "
                f"{', '.join(sorted(unknown_fields))}"
            )
        for field in ("model", "base_url", "api_key"):
            if field in section and (
                not isinstance(section[field], str) or not section[field].strip()
            ):
                raise ValueError(f"配置字段 {section_name}.{field} 必须是非空字符串")

    for field in ("use_vision", "allow_insecure_http"):
        if field in config and not isinstance(config[field], bool):
            raise ValueError(f"配置字段 {field} 必须是布尔值")

    allow_insecure_http = config.get("allow_insecure_http", False)
    for section_name in ("translate", "vision"):
        base_url = (config.get(section_name) or {}).get("base_url")
        if base_url:
            validate_base_url(
                base_url,
                allow_insecure_http=allow_insecure_http,
            )


def _apply_config(args: argparse.Namespace, config: dict) -> argparse.Namespace:
    """把配置合并进参数：仅填充命令行未显式提供的项（值为 None 的项）。"""
    translate = config.get("translate") or {}
    vision = config.get("vision") or {}

    # 翻译参数
    if args.model is None:
        args.model = translate.get("model", "deepseek-chat")
    if args.base_url is None:
        args.base_url = translate.get("base_url", "https://api.deepseek.com/v1")
    if args.api_key is None:
        args.api_key = translate.get("api_key") or None

    # 视觉参数
    if args.vision_model is None:
        args.vision_model = vision.get("model", "gpt-5.4-mini")
    if args.vision_base_url is None:
        args.vision_base_url = vision.get("base_url", "https://api.openai.com/v1")
    if args.vision_api_key is None:
        args.vision_api_key = vision.get("api_key") or None

    # 视觉自动开关可由 --no-vision 显式覆盖。
    no_vision = getattr(args, "no_vision", False)
    if config.get("use_vision", False) and not no_vision:
        args.vision = True
    if no_vision:
        args.vision = False

    # 远程 HTTP 默认拒绝；配置文件或命令行必须显式开启。
    if config.get("allow_insecure_http", False):
        args.allow_insecure_http = True

    return args


def _create_vision_reader(args: argparse.Namespace):
    from .vision import VisionReader

    return VisionReader(
        api_key=args.vision_api_key,
        model=args.vision_model,
        base_url=args.vision_base_url,
        allow_insecure_http=args.allow_insecure_http,
    )


def process_vision_only(pdf_path: str, args: argparse.Namespace) -> dict:
    """只提取并解读图片，不执行正文转换或翻译。"""
    from .figures import extract_figures
    from .vision import readings_to_markdown

    document_id = _safe_output_name(Path(pdf_path).stem, pdf_path)
    document_dir = Path(args.out_dir) / document_id
    print(f"\n=== 图片解读: {pdf_path} ===")
    figures_result = extract_figures(
        pdf_path,
        out_dir=document_dir / "figures",
        min_size=args.fig_min_size,
    )
    written = write_figures_report(figures_result, document_dir)
    print(f"  图片: {figures_result.count} 张（跳过 {figures_result.skipped_small} 张小图）")

    reader = _create_vision_reader(args)
    if not reader.available:
        raise RuntimeError("未配置视觉 API Key，无法执行 --vision-only")

    def progress(done: int, total: int, reading) -> None:
        print(f"    [解读进度] {done}/{total}（{reading.figure_path.name}）", flush=True)

    reader.on_progress = progress
    vision_result = reader.read_figures(figures_result.figures)
    md = readings_to_markdown(vision_result.readings)
    if md:
        path = document_dir / f"{Path(pdf_path).stem}.figures-reading.md"
        path.write_text(md, encoding="utf-8")
        written["reading"] = path
    print(f"  解读完成: {vision_result.ok_count}/{vision_result.total} 张成功")
    return {
        "pdf": pdf_path,
        "type": "vision_only",
        "pages": 0,
        "chars": 0,
        "chunks": 0,
        "figures": figures_result.count,
        "outputs": {key: str(path) for key, path in written.items()},
    }


def _test_api_config(args: argparse.Namespace) -> int:
    """使用最小请求验证翻译和视觉配置。"""
    failures = 0
    if args.api_key:
        try:
            translator = LLMTranslator(
                api_key=args.api_key,
                model=args.model,
                base_url=args.base_url,
                temperature=args.temperature,
                allow_insecure_http=args.allow_insecure_http,
                max_retries=1,
            )
            translator.test_connection()
            print(f"翻译 API: 成功（{args.model} @ {args.base_url}）")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"翻译 API: 失败（{exc}）", file=sys.stderr)
    else:
        failures += 1
        print("翻译 API: 未配置 Key", file=sys.stderr)

    vision_requested = bool(args.vision_api_key or args.vision)
    if vision_requested:
        try:
            reader = _create_vision_reader(args)
            if not reader.available:
                raise RuntimeError("未配置视觉 API Key")
            reader.max_retries = 1
            reader.test_connection()
            print(f"视觉 API: 成功（{args.vision_model} @ {args.vision_base_url}）")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"视觉 API: 失败（{exc}）", file=sys.stderr)
    else:
        print("视觉 API: 未配置，已跳过")
    return 1 if failures else 0


def _write_json_out(json_path: str, failures: list[tuple[str, str]], summary: list[dict]) -> None:
    """把汇总结果写入机器可读 JSON 文件（供 DSH 插件等程序化调用方解析）。"""
    import json as _json

    result = {
        "ok": not failures,
        "documents": summary,
        "failures": [{"pdf": pdf, "error": error} for pdf, error in failures],
    }
    try:
        Path(json_path).write_text(
            _json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:  # noqa: BLE001
        print(f"⚠️ 写入 JSON 结果失败: {exc}", file=sys.stderr)


def process_one(
    pdf_path: str,
    args: argparse.Namespace,
    translator: LLMTranslator | None,
) -> dict:
    print(f"\n=== 处理: {pdf_path} ===")
    document_id = _safe_output_name(Path(pdf_path).stem, pdf_path)
    document_dir = Path(args.out_dir) / document_id
    result = convert_pdf_to_markdown(
        pdf_path,
        enable_ocr=not args.no_ocr,
    )

    ocr_pages = [page + 1 for page in result.pages_needing_ocr]
    if result.ocr_skipped:
        ocr_status = "已主动跳过"
    elif result.ocr_used:
        ocr_status = "已执行"
    elif ocr_pages:
        ocr_status = "未执行/降级"
    else:
        ocr_status = "无需 OCR"
    print(
        f"  类型: {result.pdf_type} | 页数: {result.page_count} | "
        f"需OCR页: {ocr_pages or '无'} | OCR状态: {ocr_status} | "
        f"耗时: {result.processing_time_ms}ms"
    )
    for w in result.warnings:
        print(f"  ⚠️ {w}")
    if result.has_encoding_issues:
        print("  ⚠️ 检测到编码问题，部分字符可能异常")
    if result.is_complex_layout:
        print("  ℹ️ 检测到复杂版式（多栏/表格）")

    print(f"  提取 Markdown: {len(result.markdown)} 字符")

    # 分块
    chunks = chunk_markdown(result.markdown, max_tokens=args.chunk_tokens)
    print(f"  分块: {len(chunks)} 块")

    # 翻译
    translations = []
    if args.no_translate:
        from .report import build_bilingual_markdown, build_plain_chinese
        from .translate import TranslationResult

        translations = [
            TranslationResult(
                index=c.index,
                source=c.text,
                translation="",
                ok=False,
                error="已跳过翻译 (--no-translate)",
                skipped=True,
            )
            for c in chunks
        ]
        print("  跳过翻译 (--no-translate)")
    else:
        if translator is None or not translator.available:
            raise RuntimeError("翻译器不可用；请检查 API Key 配置")
        print(f"  开始翻译（模型: {translator.model} @ {translator.base_url}，{len(chunks)} 块）...")
        translations = translator.translate_document(chunks)
        ok = sum(1 for t in translations if t.ok)
        print(f"  翻译完成: {ok}/{len(chunks)} 块成功")

    # 输出
    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    written = write_report(result, chunks, translations, document_dir, formats=formats)

    # 图片提取（默认开启）：PDF 内嵌图片 → PNG + 图注匹配
    figures_result = None
    if args.figures:
        from .figures import extract_figures

        print("  提取图片...")
        figures_result = extract_figures(
            pdf_path,
            out_dir=document_dir / "figures",
            min_size=args.fig_min_size,
        )
        print(f"  图片: {figures_result.count} 张（跳过 {figures_result.skipped_small} 张小图）")
        fig_written = write_figures_report(figures_result, document_dir, formats=formats)
        written.update(fig_written)
        for f_ in figures_result.figures:
            cap = f" | {f_.caption[:60]}" if f_.caption else ""
            print(f"    {f_.path.name} ({f_.width}x{f_.height}){cap}")

        # 视觉理解（--vision 开启）：VLM 解读每张图
        if args.vision and figures_result.figures:
            from .vision import readings_to_markdown
            from pathlib import Path as _P

            vreader = _create_vision_reader(args)
            if not vreader.available:
                print("  ⚠️ 未配置视觉模型 Key（VISION_API_KEY / --vision-api-key），跳过图片解读。")
            else:
                print(f"  图片解读（模型: {vreader.model}，{len(figures_result.figures)} 张）...")

                def _vprogress(done: int, total: int, reading) -> None:
                    print(f"    [解读进度] {done}/{total}（{reading.figure_path.name}）", flush=True)

                vreader.on_progress = _vprogress
                vision_res = vreader.read_figures(figures_result.figures)
                print(f"  解读完成: {vision_res.ok_count}/{vision_res.total} 张成功")

                md = readings_to_markdown(vision_res.readings)
                if md:
                    stem = _P(pdf_path).stem
                    vp = document_dir / f"{stem}.figures-reading.md"
                    vp.write_text(md, encoding="utf-8")
                    written["reading"] = vp

    print("  输出文件:")
    for k, v in written.items():
        print(f"    [{k}] {v}")

    return {
        "pdf": pdf_path,
        "type": result.pdf_type,
        "pages": result.page_count,
        "chars": len(result.markdown),
        "chunks": len(chunks),
        "figures": figures_result.count if figures_result else 0,
        "outputs": {k: str(v) for k, v in written.items()},
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 合并 skill 配置文件（命令行显式参数优先）
    try:
        config = _load_config(args.config)
        args = _apply_config(args, config)
    except ValueError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    invalid_formats = sorted(set(formats) - {"md", "zh", "html"})
    if not formats or invalid_formats:
        detail = ", ".join(invalid_formats) if invalid_formats else "空值"
        print(f"配置错误: 不支持的输出格式: {detail}；允许 md,zh,html", file=sys.stderr)
        return 2
    args.formats = ",".join(dict.fromkeys(formats))

    if args.allow_insecure_http:
        print("⚠️ 已允许远程 HTTP API：API Key、论文文本和图片将以未加密方式传输。")
        print("   仅应对你信任的中转站开启 allow_insecure_http。")

    if args.test_config:
        return _test_api_config(args)
    if not args.pdfs:
        print("配置错误: 请提供至少一个 PDF 文件路径", file=sys.stderr)
        return 2
    if args.vision_only:
        args.vision = True
        args.no_translate = True
        if not args.vision_api_key:
            print("配置错误: --vision-only 需要视觉 API Key", file=sys.stderr)
            return 2

    translator: LLMTranslator | None = None
    if not args.no_translate and not args.vision_only:
        translator = LLMTranslator(
            api_key=args.api_key,
            model=args.model,
            base_url=args.base_url,
            temperature=args.temperature,
            allow_insecure_http=args.allow_insecure_http,
            on_progress=_progress,
        )
        if not translator.available:
            print(
                "未检测到翻译 API Key。请设置 LLM_API_KEY（兼容 DEEPSEEK_API_KEY / "
                "OPENAI_API_KEY），或使用 --api-key；若只需提取文本，请使用 --no-translate。",
                file=sys.stderr,
            )
            return 2

    # 展开通配符，支持 *.pdf 批量处理
    pdf_files: list[str] = []
    for pat in args.pdfs:
        if any(ch in pat for ch in "*?["):
            matched = sorted(glob.glob(pat))
            if not matched:
                print(f"⚠️ 通配符无匹配: {pat}")
            pdf_files.extend(matched)
        else:
            pdf_files.append(pat)
    if not pdf_files:
        print("没有可处理的 PDF 文件。")
        return 1

    summary = []
    failures: list[tuple[str, str]] = []
    for pdf in pdf_files:
        path = Path(pdf)
        if not path.is_file():
            failures.append((pdf, "文件不存在或不是普通文件"))
            print(f"\n处理失败: {pdf}: 文件不存在或不是普通文件", file=sys.stderr)
            continue
        if path.suffix.lower() != ".pdf":
            failures.append((pdf, "不是 PDF 文件"))
            print(f"\n处理失败: {pdf}: 不是 PDF 文件", file=sys.stderr)
            continue
        try:
            if args.vision_only:
                summary.append(process_vision_only(pdf, args))
            else:
                summary.append(process_one(pdf, args, translator))
        except Exception as exc:  # noqa: BLE001
            failures.append((pdf, str(exc)))
            print(f"\n处理失败: {pdf}: {exc}", file=sys.stderr)

    print("\n=== 汇总 ===")
    for s in summary:
        print(f"  {s['pdf']}: {s['type']} / {s['pages']}页 / {s['chars']}字符 / {s['chunks']}块 / {s['figures']}图")
    for pdf, error in failures:
        print(f"  {pdf}: 失败 / {error}")
    print(f"\n完成: {len(summary)} 篇成功，{len(failures)} 篇失败。")
    if args.json_out:
        _write_json_out(args.json_out, failures, summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
