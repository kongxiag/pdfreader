# dsh-tool-pdfreader

> 中文版 · English: [README.md](README.md)

把 [`pdf-reader`](../)（Python）包装成 DeepSeek Harness 的**工具插件**：模型直接调用
`pdfreader` 工具，完成「PDF → 分类 → 提取/OCR → 分块 → 中文翻译 → 图片提取/解读 → 报告」，
并拿到结构化的输出文件清单。

> 与 `skill`（指令型技能）不同：插件给模型注册了一个**类型化工具**——参数有 schema、
> 结果有规范 JSON、可参与并行/Code Mode/UI 卡片。技能走「教模型敲命令」，插件走「模型调函数」。
> 两者共用同一份 `config.json`。

## 前置条件

1. Python 3.10+，已 `pip install -r ../requirements.txt`（pdf-inspector、PyMuPDF 等）。
2. 翻译/视觉的 `config.json`（与 skill 共用一份，格式见 `../.agents/skills/pdfreader/config.example.json`）。
   - 图片**提取**不需要视觉模型；图片**解读**需在 `config.json` 配一个 OpenAI 兼容视觉模型
     （如 gpt-4o / Qwen-VL / GLM-4V），因为 DeepSeek 文本模型本身不能看图。

## 构建

`lib/index.js` 是**自包含 bundle**：用 esbuild 把唯一的运行时依赖 `schemastery` 内联进去，
产物仅依赖 Node 内置模块（`node:fs` / `node:crypto` / `node:path`），**零 peer 依赖**。
这彻底规避了 `dsh plugin add <本地目录>` 用 `link:` 符号链接时、Node realpath 导致 peer
依赖解析失败的问题（`@deepseek-ai/dsh-tools` 等仅作为 `import type` 在编译期使用，不进入产物）。

`lib/` 已预构建，可直接安装。如需重新构建：

```bash
cd tool-pdfreader
npm install          # esbuild / typescript / @types/node / @deepseek-ai/*（仅构建期）
npm run build        # tsc 类型检查 + 声明生成 + esbuild 打包 → lib/
```

## 安装到 DSH

### 方式一：一键脚本（推荐）

```bash
node tool-pdfreader/setup.mjs            # 自动安装 + 注册 + 自检
node tool-pdfreader/setup.mjs --dry-run  # 先看计划，不实际改动
```

脚本会：探测 `dsh` 并用 `dsh plugin add` 安装（没有 `dsh`/`pnpm` 时自动降级为纯 Node
「建符号链接 + 写 cordis.patch.yml」），然后自检（Python `--doctor` + 插件加载冒烟），
最后提示重启。幂等，可重复运行。

### 方式二：手工三步

### 第 1 步：把包装进 profile

```bash
dsh plugin --profile web add /path/to/pdf-reader/tool-pdfreader   # 换成你的实际路径
```

`dsh plugin` 转发给 pnpm，把本目录作为依赖装进
`<DSH_HOME>/profiles/<profile>/node_modules`。因为 `lib/index.js` 是自包含 bundle（无 peer 依赖），
无论 pnpm 用 `link:` 还是 `file:` 安装，都不存在 peer 解析问题。

### 第 2 步：注册进 `cordis.patch.yml`

编辑 `<DSH_HOME>/profiles/<profile>/cordis.patch.yml`，追加（可直接复制
[`cordis.patch.example.yml`](./cordis.patch.example.yml)）：

```yaml
- insert:
    - id: tool-pdfreader
      name: 'dsh-tool-pdfreader'
      config:
        # cwd / configPath 均可省略：cwd 自动定位到插件所在仓库根，configPath 自动尝试
        # 仓库根下的 .agents/skills/pdfreader/config.json。仅非标准布局时才需显式填写。
        pythonBin: python
        defaultFormats: md,zh,html
        outDir: output
        timeoutMs: 600000
```

### 第 3 步：重启 DSH

重启后模型即可调用 `pdfreader` 工具。

> Web 界面说明：模型可见的工具由 **agent preset** 组装；本插件注册在 host 层的全局工具
> registry 里（scope 链 `agent → preset → global`），会合并进每个 agent 的工具视图。
> 若想按 preset 精细控制，可复制一个用户 preset（`$DSH_HOME/.agent-presets/<id>/`）并
> 在它的 `agent.cordis.yml` 里用绝对路径引用本插件。

## 配置项

| 字段 | 默认 | 说明 |
|---|---|---|
| `cwd` | 自动 | `python -m pdfreader` 的运行目录；默认自动定位仓库根，仅 `file:`/npm 安装或非标准布局时需显式填写 |
| `pythonBin` | `python` | Python 可执行文件（裸名走 PATH 或绝对路径） |
| `configPath` | 自动 | 默认 config.json；空串时自动尝试 `<cwd>/.agents/skills/pdfreader/config.json`（存在才传） |
| `defaultFormats` | `md,zh,html` | 默认输出格式 |
| `outDir` | `output` | 输出目录（相对 `cwd` 或绝对） |
| `timeoutMs` | `600000` | 单次协作超时预算（大文献翻译可能数分钟） |
| `maxOutputBytes` | `64000` | stdout/stderr 收集上限（仅诊断用，结果走 `--json-out`） |

## 工具参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `pdfs` | string[]（必填） | PDF 路径，支持多个 / 通配符 |
| `formats` | string | `md,zh,html` 逗号分隔 |
| `out_dir` | string | 覆盖插件配置的输出目录 |
| `no_translate` | boolean | 只提取，不翻译 |
| `vision` | boolean | 外部视觉模型解读图片（需 vision 配置） |
| `vision_only` | boolean | 只提取并解读图片 |
| `no_figures` | boolean | 关闭图片提取 |
| `chunk_tokens` | integer | 每块最大 token |
| `config` | string | 覆盖插件配置的 config.json 路径 |

API Key **不**进入工具参数：一律从 `config.json` 读取，避免 Key 出现在调用历史/日志里。

## 返回

结构化 JSON（也经 `render` 转成模型可读文本）：

```json
{
  "ok": true,
  "documents": [{
    "pdf": "...", "type": "text_based", "pages": 9,
    "chars": 52181, "chunks": 5, "figures": 2,
    "outputs": { "md": "...\\x.bilingual.md", "zh": "...", "html": "..." }
  }],
  "failures": [{ "pdf": "...", "error": "..." }]
}
```

## 实现要点与安全

- 通过 `ctx.subprocess.spawn` 以 **argv 形式**直接拉起 Python（不经 shell），规避 Windows
  路径含空格/CJK 的引号转义；`ctx.subprocess` 负责受管进程树、有界输出、取消升级。
- 结果不靠解析 stdout：CLI 的 `--json-out` 把权威结果写入工作区内的临时 JSON，插件读取后即删。
- `exec.signal` 与 `AbortSignal.timeout` 融合后传给子进程，取消/超时会触发进程树终止。
- 插件以 harness 进程权限运行 Python（与 bash/pwsh 工具同级信任）；如需沙箱/审批门禁，
  另在 profile 挂 `tools/pre-execute` 或 sandbox 执行器。
