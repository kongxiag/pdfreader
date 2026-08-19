# pdfreader — 基于 pdf-inspector 的 AI 文献阅读工具

> 中文版 · English: [README.md](README.md)

把外文 PDF 文献快速转换为**结构化 Markdown**，并通过**任意 OpenAI 兼容 LLM**
（默认 DeepSeek，可换 OpenAI / Qwen / GLM / 中转站等）翻译为中文，
输出**中英对照阅读报告**，显著提升 AI 与人对文献的阅读效率。

```
PDF 文献 ─► pdf-inspector 分类 ─► 文本型:直接提取 Markdown / 扫描型:OCR
        ─► 按标题/段落分块 ─► LLM 逐块翻译(术语一致, 模型可换)
        ─► 输出: 中英对照 md / 纯中文 md / HTML 报告 / 原始提取文本
        ─► 图片提取 + 视觉模型(VLM) 解读（可选）
```

## 为什么用 pdf-inspector

- **智能路由**：先快速分类 PDF 是文本型还是扫描型，避免对文本型 PDF 盲目跑 OCR（慢且贵）
- **高质量提取**：Rust 实现，速度极快（测试文献 10 页 42ms），输出结构化 Markdown
  （保留标题、表格、版式信息）
- **按需 OCR**：扫描件才初始化 OCR 运行时，`auto` 模式自动判断

## 安装

```bash
# 需要 Python 3.10+，建议 3.12/3.13（3.14 亦可）
cd pdf-reader
pip install -r requirements.txt
```

## 快速开始

```bash
# 1) 单篇：转换 + 翻译 + 中英对照报告（推荐将 API 配置写入本地 config.json）
python -m pdfreader ..\paper.pdf --config .\config.json

# 2) 处理前发送最小请求验证翻译/视觉配置
python -m pdfreader --test-config --config .\config.json

# 3) 换用其他模型/服务（OpenAI 兼容接口，任意 base_url）
python -m pdfreader ..\paper.pdf --model gpt-4o --base-url "https://api.openai.com/v1"
python -m pdfreader ..\paper.pdf --model qwen-max --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 4) 输出 HTML 网页版报告（浏览器打开，原文译文分栏）
python -m pdfreader ..\paper.pdf --formats md,html

# 5) 批量处理文件夹里所有 PDF
python -m pdfreader ..\*.pdf --out-dir ..\output

# 6) 只转换提取，不调 API（离线验证流程）
python -m pdfreader ..\paper.pdf --no-translate
```

## 翻译模型配置

翻译走 **OpenAI 兼容接口**，三项均可配置，默认指向 DeepSeek：

| 配置 | 参数 | 环境变量 |
|---|---|---|
| 模型名 | `--model`（默认 `deepseek-chat`） | — |
| 接口地址 | `--base-url`（默认 `https://api.deepseek.com/v1`） | — |
| API Key | `--api-key` 或配置文件中的 `translate.api_key` | `LLM_API_KEY`（兼容 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`） |

兼容标准 `/chat/completions` 的服务皆可用：DeepSeek、OpenAI、通义千问 Qwen（DashScope 兼容模式）、
智谱 GLM、本地 Ollama（`--base-url http://localhost:11434/v1`）等。远程接口默认必须使用 HTTPS；
本地 localhost HTTP 自动允许。若可信中转站只有远程 HTTP，可显式添加 `--allow-insecure-http`，
或在配置文件根级设置 `"allow_insecure_http": true`。此时 API Key 和论文内容会以未加密方式传输。

配置文件可保存 `model`、`base_url`、`api_key` 和开关，便于本地使用。API Key 会以明文形式保存，
因此配置文件必须保持在本机，不要提交或分享；skill 的 `config.json` 已加入工作区 `.gitignore`。
CLI 会拒绝未知字段、空值、错误类型或非法 URL，并返回非零退出码。环境变量仍可作为可选方式使用。

## 命令行参数

| 参数 | 说明 |
|---|---|
| `pdfs` | PDF 路径，支持多个/通配符 |
| `-o, --out-dir` | 输出目录（默认 `output/`） |
| `--formats` | 输出格式：`md`(中英对照)、`zh`(纯中文)、`html`(网页)，逗号分隔 |
| `--no-translate` | 只转换提取，跳过翻译 |
| `--no-ocr` | 禁用扫描件 OCR；仍报告检测到的页码并标记“已主动跳过” |
| `--no-figures` | 关闭图片提取（默认开启） |
| `--fig-min-size` | 图片过滤尺寸（默认 80px，过滤小图标） |
| `--vision` | 在完整流程中用视觉模型解读图片（需配置视觉 Key） |
| `--no-vision` | 显式禁用视觉处理，可覆盖配置中的 `use_vision=true` |
| `--vision-only` | 只提取和解读图片，不转换或翻译正文 |
| `--test-config` | 发送最小请求测试翻译/视觉 URL、Key 和模型，不处理 PDF |
| `--vision-model` | 视觉模型名（默认 `gpt-5.4-mini`，可换 gpt-4o/Qwen-VL 等） |
| `--vision-base-url` | 视觉模型接口地址（OpenAI 兼容，默认 OpenAI 官方；中转站填其 `/v1` 地址） |
| `--vision-api-key` | 视觉 API Key（默认读 `VISION_API_KEY` / `OPENAI_API_KEY` 环境变量） |
| `--chunk-tokens` | 每块最大 token（默认 3500，长文调大） |
| `--model` | 翻译模型名（默认 `deepseek-chat`，可换任意 OpenAI 兼容模型） |
| `--base-url` | 翻译接口地址（默认 DeepSeek；其他服务/中转站填其 `/v1` 地址） |
| `--api-key` | 翻译 API Key（默认读 `LLM_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`） |
| `--config` | JSON 配置文件，提供翻译/视觉参数与安全开关 |
| `--allow-insecure-http` | 显式允许远程 HTTP API；连接不加密，默认拒绝 |
| `--temperature` | 翻译温度（默认 1.0） |

## 输出文件

每篇 PDF 写入独立的 `<输出目录>/<文献名-路径哈希>/`，同名 PDF 不会覆盖。该目录内包含：

| 文件 | 内容 |
|---|---|
| `<名>.bilingual.md` | 中英对照 Markdown（逐块：原文 → 译文） |
| `<名>.zh.md` | 纯中文译文 Markdown |
| `<名>.report.html` | 单文件 HTML 报告（可离线打开） |
| `<名>.extracted.md` | pdf-inspector 原始提取文本（可再喂给任意 AI） |
| `<名>.figures.md` | 文献图片清单（含本地图片引用与图注） |
| `<名>.figures-reading.md` | 图表解读（`--vision` 开启时由 VLM 生成） |
| `figures/` 目录 | 当前文献提取的图片 PNG；外层文献目录已按路径哈希隔离 |

## 图片提取与理解

pdf-inspector 只提取文本，图片会丢失。pdfreader 分两步补充：

1. **图片提取**（默认开启）：PyMuPDF 逐页提取 PDF 内嵌图片（自动过滤小图标），
   从页面文本定位图注（"Fig. N"）并按空间位置自动匹配，图片保存在文献独立目录内的 `figures/`，
   清单写入 `<名>.figures.md`。
2. **图片理解**（`--vision` 开启）：调用视觉模型（VLM）逐图生成结构化中文解读——
   图表类型、内容概述、关键元素、信息要点、与论文的关系，写入
   `<名>.figures-reading.md`。

视觉模型走 OpenAI 兼容接口，`--vision-base-url` 可配任意兼容服务
（OpenAI 官方 / 通义千问 / 智谱 / 中转站网关等）。

```bash
# 官方 OpenAI
python -m pdfreader paper.pdf --vision

# 中转站/兼容服务（远程 HTTP 必须显式确认风险）
python -m pdfreader paper.pdf --vision --vision-base-url "http://xxx:3001/v1" --vision-model gpt-5.4-mini --allow-insecure-http

# 已翻译过正文，只补做图片解读
python -m pdfreader paper.pdf --vision-only --config .\config.json
```

> 提示：翻译（LLM）与图片理解（视觉模型）是两条独立链路，可分别配置 Key 与模型。

## 独立使用图片提取

```bash
python -m pdfreader.figures paper.pdf -o output/figures
```

## 工作流程说明

1. **分类**：`classify_pdf` 判断类型与需 OCR 的页
2. **提取**：文本型走 `process_pdf`；有扫描页走 `process_pdf_with_ocr`（auto 模式）
3. **分块**：按标题/段落切块，相邻块保留重叠，适配 token 上限
4. **翻译**：LLM 逐块翻译（默认 `deepseek-chat`，可换任意 OpenAI 兼容模型），
   自动提取术语并回填术语表，保证全文术语一致；仅对超时、429 和 5xx 临时错误重试，永久错误立即失败
5. **报告**：生成中英对照/纯中文/HTML 报告

## 项目结构

```
pdf-reader/
├── requirements.txt
├── pdfreader/
│   ├── __init__.py     # 包入口
│   ├── cli.py          # 命令行入口
│   ├── convert.py      # pdf-inspector 封装：分类 + 提取 + OCR 路由
│   ├── chunk.py        # 分块策略
│   ├── translate.py    # LLM 翻译（OpenAI 兼容，术语表 + 重试）
│   ├── report.py       # 报告生成（md/zh/html）
│   ├── figures.py      # 图片提取 + 图注匹配（PyMuPDF）
│   └── vision.py       # 图片理解（视觉模型 VLM）
└── output/             # 生成的报告与图片（--out-dir 可改）
```

## DSH 集成（skill + 工具插件）

本仓库同时提供两种 DeepSeek Harness 扩展，两种都会**自动加载**：

| 形态 | 位置 | 说明 |
|---|---|---|
| **Skill（技能）** | `.agents/skills/pdfreader/SKILL.md` | DSH 启动时自动扫描发现，零安装；教模型按流程调用 CLI |
| **工具插件** | `tool-pdfreader/` | 模型直接调用 `pdfreader` 工具；需 `dsh plugin add` + 注册（见 `tool-pdfreader/README.md`） |

### Skill：自动发现，零安装

skill 放在 DSH 标准位置 `.agents/skills/pdfreader/SKILL.md`（项目根 = 含 `.git` 的最近祖先）。
在 `pdf-reader` 目录或其下打开 DSH 会话时，`skill-filesystem` 会自动发现并把它列进可用技能。

> 若你在**上一级目录**（而非 `pdf-reader` 仓库目录）打开会话，DSH 的项目根会回退为该目录；
> 此时可把 `.agents/skills/pdfreader` 手动复制到该目录的 `.agents/skills/` 下（或直接把会话
> 开在仓库目录内）。

### 工具插件：一次注册，自动挂载

1. 安装：`dsh plugin --profile web add tool-pdfreader`
2. 注册：把 `tool-pdfreader/cordis.patch.example.yml` 的内容并入 profile 的 `cordis.patch.yml`
3. 重启 DSH 后，模型即可直接调用 `pdfreader` 工具。

详见 [`tool-pdfreader/README.md`](tool-pdfreader/README.md)。

## 后续可扩展

- 扫描件 OCR 模型本地缓存（`--model-directory`）
- AI 摘要 / 要点提取 / 关键术语表自动生成
- 图片理解已支持（`--vision`）：可接入任意 OpenAI 兼容视觉模型；本地
  Ollama + Qwen2.5-VL 亦可（填本地 base_url）
- DeepSeek Harness 集成：已提供 skill（`.agents/skills/pdfreader/`）与工具插件
  （`tool-pdfreader/`），见上文「DSH 集成」
- 公式/图表（pdf-inspector 提取的 Markdown 保留结构，可后续解析图表）
