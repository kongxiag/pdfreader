# pdfreader — an AI literature-reading tool built on pdf-inspector

> English · 中文版：[README.zh.md](README.zh.md)

Convert foreign-language PDF papers into **structured Markdown**, translate them into
Chinese with **any OpenAI-compatible LLM** (DeepSeek by default; OpenAI / Qwen / GLM /
relays also work), and produce **bilingual reading reports** — making it far faster for
both humans and AI to read academic literature.

```
PDF  ─► pdf-inspector classifies ─► text PDF: extract Markdown directly / scanned PDF: OCR
     ─► split by heading/paragraph ─► LLM translates block by block (consistent terminology)
     ─► output: bilingual md / Chinese-only md / HTML report / raw extracted text
     ─► figure extraction + vision-model (VLM) interpretation (optional)
```

## Why pdf-inspector

- **Smart routing**: classifies a PDF as text-based or scanned first, avoiding wasteful
  OCR on text-based PDFs (slow and costly).
- **High-quality extraction**: implemented in Rust, extremely fast (42 ms for a 10-page
  test paper), and produces structured Markdown that preserves headings, tables, and layout.
- **On-demand OCR**: the OCR runtime is only initialized for scanned pages; `auto` mode
  decides automatically.

## Installation

```bash
# Requires Python 3.10+ (3.12/3.13 recommended; 3.14 also works)
cd pdf-reader
pip install -r requirements.txt
```

## Quick start

```bash
# 1) Single paper: convert + translate + bilingual report
#    (recommended: save your API config to a local config.json)
python -m pdfreader ..\paper.pdf --config .\config.json

# 2) Verify translation/vision config with a minimal request before processing
python -m pdfreader --test-config --config .\config.json

# 3) Use another model/service (OpenAI-compatible, any base_url)
python -m pdfreader ..\paper.pdf --model gpt-4o --base-url "https://api.openai.com/v1"
python -m pdfreader ..\paper.pdf --model qwen-max --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 4) Also emit an HTML report (open in a browser; source and translation side by side)
python -m pdfreader ..\paper.pdf --formats md,html

# 5) Batch-process all PDFs in a folder
python -m pdfreader ..\*.pdf --out-dir ..\output

# 6) Extraction only, no API calls (offline pipeline check)
python -m pdfreader ..\paper.pdf --no-translate
```

## Translation model configuration

Translation goes through an **OpenAI-compatible API**. All three values are configurable
and default to DeepSeek:

| Setting | Flag | Environment variable |
|---|---|---|
| Model | `--model` (default `deepseek-chat`) | — |
| Endpoint | `--base-url` (default `https://api.deepseek.com/v1`) | — |
| API key | `--api-key` or `translate.api_key` in config | `LLM_API_KEY` (also reads `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`) |

Any service that speaks the standard `/chat/completions` API works: DeepSeek, OpenAI,
Qwen (DashScope compatibility mode), GLM (Zhipu), local Ollama
(`--base-url http://localhost:11434/v1`), and more. Remote endpoints must use HTTPS by
default; localhost HTTP is always allowed. If a trusted relay only offers remote HTTP,
pass `--allow-insecure-http` or set `"allow_insecure_http": true` at the root of the
config file — note that your API key and paper content will then be sent unencrypted.

A config file can store `model`, `base_url`, `api_key`, and switches for local convenience.
The API key is stored in plain text, so keep the file on your own machine and never commit
or share it; the skill's `config.json` is already git-ignored. The CLI rejects unknown
fields, empty values, wrong types, and unsafe URLs with a non-zero exit code. Environment
variables remain an optional alternative.

## Command-line options

| Option | Description |
|---|---|
| `pdfs` | PDF paths; supports multiple paths and globs |
| `-o, --out-dir` | Output directory (default `output/`) |
| `--formats` | Output formats, comma-separated: `md`(bilingual), `zh`(Chinese-only), `html`(web page) |
| `--no-translate` | Extract only, skip translation |
| `--skip-references` | Skip translating the references/bibliography section, keeping the original (default on) |
| `--no-skip-references` | Translate everything, including the references |
| `--no-ocr` | Disable OCR for scanned pages; still reports detected pages and marks them "skipped" |
| `--no-figures` | Disable figure extraction (enabled by default) |
| `--fig-min-size` | Figure size filter (default 80 px, drops small icons) |
| `--vision` | Interpret figures with a vision model in the full pipeline (requires a vision key) |
| `--no-vision` | Explicitly disable vision, overriding `use_vision=true` in config |
| `--vision-only` | Extract and interpret figures only; skip text conversion/translation |
| `--test-config` | Send a minimal request to test translation/vision URL, key, and model |
| `--vision-model` | Vision model name (default `gpt-5.4-mini`; gpt-4o / Qwen-VL etc. work) |
| `--vision-base-url` | Vision endpoint (OpenAI-compatible; default OpenAI official; relays use their `/v1`) |
| `--vision-api-key` | Vision API key (defaults to `VISION_API_KEY` / `OPENAI_API_KEY`) |
| `--chunk-tokens` | Max tokens per chunk (default 3500; raise for long papers) |
| `--model` | Translation model name (default `deepseek-chat`) |
| `--base-url` | Translation endpoint (default DeepSeek) |
| `--api-key` | Translation API key (defaults to `LLM_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`) |
| `--config` | JSON config file providing translation/vision defaults and security switches |
| `--allow-insecure-http` | Explicitly allow remote HTTP APIs (unencrypted; rejected by default) |
| `--temperature` | Translation temperature (default 1.0) |

## Output files

Each PDF writes to its own `<output-dir>/<paper-name-path-hash>/`, so same-named PDFs
never overwrite each other. That directory contains:

| File | Contents |
|---|---|
| `<name>.bilingual.md` | Bilingual Markdown (block by block: source → translation) |
| `<name>.zh.md` | Chinese-only translation Markdown |
| `<name>.report.html` | Single-file HTML report (open offline) |
| `<name>.extracted.md` | Raw pdf-inspector extraction (can feed any AI) |
| `<name>.figures.md` | Figure manifest (local image refs + captions) |
| `<name>.figures-reading.md` | Figure interpretation (generated by a VLM with `--vision`) |
| `figures/` directory | Extracted PNGs for the paper, isolated by path hash |

## Figure extraction and understanding

pdf-inspector only extracts text, so images are lost. pdfreader restores them in two steps:

1. **Figure extraction** (enabled by default): PyMuPDF extracts embedded images page by
   page (auto-filtering small icons), locates captions ("Fig. N") from page text, and
   matches them spatially. Images go into the paper's own `figures/` directory and the
   manifest is written to `<name>.figures.md`.
2. **Figure understanding** (with `--vision`): a vision model (VLM) generates a structured
   Chinese interpretation per image — chart type, overview, key elements, takeaways, and
   relation to the paper — written to `<name>.figures-reading.md`.

The vision model uses an OpenAI-compatible API; `--vision-base-url` works with any
compatible service (OpenAI official / Qwen / Zhipu / relay gateways).

```bash
# Official OpenAI
python -m pdfreader paper.pdf --vision

# Relay / compatible service (remote HTTP must be explicitly acknowledged)
python -m pdfreader paper.pdf --vision --vision-base-url "http://xxx:3001/v1" --vision-model gpt-5.4-mini --allow-insecure-http

# Text already translated; only do figure interpretation
python -m pdfreader paper.pdf --vision-only --config .\config.json
```

> Note: translation (LLM) and figure understanding (vision model) are two independent
> pipelines; their keys and models are configured separately.

## Standalone figure extraction

```bash
python -m pdfreader.figures paper.pdf -o output/figures
```

## Pipeline

1. **Classify**: `classify_pdf` determines the PDF type and which pages need OCR.
2. **Extract**: text-based PDFs go through `process_pdf`; scanned pages go through
   `process_pdf_with_ocr` (auto mode).
3. **Chunk**: split by heading/paragraph with overlap between adjacent chunks to fit the
   token budget.
4. **Translate**: LLM translates block by block (default `deepseek-chat`), extracting and
   reusing a glossary to keep terminology consistent; retries only timeouts, 429s, and 5xx
   transient errors — permanent errors fail immediately.
5. **Report**: generate bilingual / Chinese-only / HTML reports.

## Project structure

```
pdf-reader/
├── requirements.txt
├── pdfreader/
│   ├── __init__.py     # package entry
│   ├── cli.py          # command-line entry
│   ├── convert.py      # pdf-inspector wrapper: classify + extract + OCR routing
│   ├── chunk.py        # chunking strategy
│   ├── translate.py    # LLM translation (OpenAI-compatible, glossary + retry)
│   ├── report.py       # report generation (md/zh/html)
│   ├── figures.py      # figure extraction + caption matching (PyMuPDF)
│   └── vision.py       # figure understanding (vision model / VLM)
└── output/             # generated reports and images (--out-dir can change this)
```

## DSH integration (skill + tool plugin)

This repository ships two DeepSeek Harness extensions, both of which **auto-load**:

| Form | Location | Notes |
|---|---|---|
| **Skill** | `.agents/skills/pdfreader/SKILL.md` | Auto-discovered by DSH at startup, zero install; teaches the model to drive the CLI |
| **Tool plugin** | `tool-pdfreader/` | The model calls a `pdfreader` tool directly; install via `dsh plugin add` + register (see `tool-pdfreader/README.md`) |

### Skill: auto-discovered, zero install

The skill sits at the standard DSH location `.agents/skills/pdfreader/SKILL.md` (project
root = the nearest ancestor containing `.git`). Open a DSH session in `pdf-reader` or below
and `skill-filesystem` discovers it automatically.

> If you open a session in the **parent directory** instead of the `pdf-reader` repo, DSH's
> project root falls back to that directory — copy `.agents/skills/pdfreader` to its
> `.agents/skills/` then (or just open the session inside the repo).

### Tool plugin: register once, auto-mounts

1. Install: `dsh plugin --profile web add tool-pdfreader` (or run `node tool-pdfreader/setup.mjs`)
2. Register: merge `tool-pdfreader/cordis.patch.example.yml` into the profile's `cordis.patch.yml`
3. Restart DSH — the model can then call the `pdfreader` tool directly.

See [`tool-pdfreader/README.md`](tool-pdfreader/README.md) for details.

## Roadmap

- Local caching of the OCR model for scanned PDFs (`--model-directory`)
- AI summary / key-point extraction / automatic glossary generation
- Figure understanding is already supported (`--vision`): any OpenAI-compatible vision
  model works; local Ollama + Qwen2.5-VL also works (set a local base_url)
- DeepSeek Harness integration: a skill (`.agents/skills/pdfreader/`) and a tool plugin
  (`tool-pdfreader/`) are provided — see "DSH integration" above
- Formulas/charts (pdf-inspector's Markdown preserves structure for later parsing)
