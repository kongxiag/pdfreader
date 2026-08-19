/**
 * DSH 工具插件：`pdfreader`
 *
 * 把 pdf-reader（Python）的 `python -m pdfreader` CLI 包装成一个模型可直接调用的
 * 工具：给定 PDF 路径，执行「分类 → 提取/OCR → 分块 → 翻译 → 图片提取/解读」流水线，
 * 并返回结构化的输出文件清单。翻译/视觉的 API Key 等敏感配置不进入工具参数，
 * 而是通过插件配置里的 `configPath` 指向本机的 config.json。
 *
 * 运行依赖：`ctx.subprocess`（DSH 的受管子进程 seam），
 * 因此必须以 argv 形式直接拉起 Python，避免 shell 引号转义问题（Windows 路径含空格/CJK）。
 *
 * 自包含说明：本文件不使用 `defineTool`，而是手写 `ToolDefinition` 并直接
 * `ctx.tools.register(...)`。除 `schemastery`（仅用于 `Config` 的 Standard Schema）外，
 * 其余 `@deepseek-ai/*` 均为 `import type`（编译后无运行时代码）。这样 esbuild 打包时
 * 只需内联 `schemastery` 一个极小的依赖，产物 `lib/index.js` 零外部依赖，
 * 彻底免疫「link: 安装导致 peer 依赖 realpath 解析失败」的问题。
 */
import { promises as fs } from 'node:fs'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import type { ToolDefinition, JsonSchemaNode } from '@deepseek-ai/dsh-tools'
import type { SubprocessHandle } from '@deepseek-ai/dsh-subprocess'
import z from '@deepseek-ai/schemastery'

export const name = 'tool-pdfreader'
export const inject = ['tools', 'subprocess']

/** 插件所在仓库根：lib/index.js 位于 <repo>/tool-pdfreader/lib/，向上两级即仓库根。
 *  仅在 link: 安装（插件 realpath 落在仓库内）时可靠；file:/npm 安装需显式配置 cwd。 */
const DEFAULT_REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

/** 插件配置：在 cordis.patch.yml / profile 中提供。 */
export interface Config {
  /** pdf-reader 项目目录（`python -m pdfreader` 的运行目录）；空串表示自动从插件位置推导仓库根。 */
  cwd: string
  /** Python 可执行文件：裸名走 PATH，或绝对路径。 */
  pythonBin: string
  /** 默认 config.json 绝对路径；空串表示自动尝试 <cwd>/.agents/skills/pdfreader/config.json。 */
  configPath: string
  /** 默认输出格式（md/zh/html 逗号分隔）。 */
  defaultFormats: string
  /** 输出目录（相对 cwd 或绝对路径）。 */
  outDir: string
  /** 单次调用的协作超时预算（ms）。 */
  timeoutMs: number
  /** stdout/stderr 收集的内存上限（字节），仅用于诊断展示。 */
  maxOutputBytes: number
}

export const Config = z.object({
  cwd: z.string().default(''),
  pythonBin: z.string().default('python'),
  configPath: z.string().default(''),
  defaultFormats: z.string().default('md,zh,html'),
  outDir: z.string().default('output'),
  timeoutMs: z.number().default(600000),
  maxOutputBytes: z.number().default(64000),
})

interface DocumentResult {
  pdf: string
  type: string
  pages: number
  chars: number
  chunks: number
  figures: number
  outputs: Record<string, string>
}
interface FailureResult {
  pdf: string
  error: string
}
interface RunResult {
  ok: boolean
  documents: DocumentResult[]
  failures: FailureResult[]
}

interface PdfreaderArgs {
  pdfs: string[]
  formats?: string
  out_dir?: string
  no_translate?: boolean
  vision?: boolean
  vision_only?: boolean
  no_figures?: boolean
  chunk_tokens?: number
  config?: string
}

/** 手写参数校验（defineTool 的参数校验在去掉后由这里承担）。 */
function parseArgs(raw: unknown): PdfreaderArgs {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new Error('pdfreader: 参数必须是对象')
  }
  const a = raw as Record<string, unknown>

  const pdfs = a.pdfs
  if (!Array.isArray(pdfs) || pdfs.length === 0 || pdfs.some((p) => typeof p !== 'string')) {
    throw new Error('pdfreader: pdfs 必须是非空字符串数组')
  }

  const str = (v: unknown, label: string): string | undefined => {
    if (v === undefined) return undefined
    if (typeof v !== 'string') throw new Error(`pdfreader: ${label} 必须是字符串`)
    return v
  }
  const bool = (v: unknown, label: string): boolean | undefined => {
    if (v === undefined) return undefined
    if (typeof v !== 'boolean') throw new Error(`pdfreader: ${label} 必须是布尔值`)
    return v
  }
  const int = (v: unknown, label: string): number | undefined => {
    if (v === undefined) return undefined
    if (typeof v !== 'number' || !Number.isInteger(v)) throw new Error(`pdfreader: ${label} 必须是整数`)
    return v
  }

  return {
    pdfs: pdfs as string[],
    formats: str(a.formats, 'formats'),
    out_dir: str(a.out_dir, 'out_dir'),
    no_translate: bool(a.no_translate, 'no_translate'),
    vision: bool(a.vision, 'vision'),
    vision_only: bool(a.vision_only, 'vision_only'),
    no_figures: bool(a.no_figures, 'no_figures'),
    chunk_tokens: int(a.chunk_tokens, 'chunk_tokens'),
    config: str(a.config, 'config'),
  }
}

const DESCRIPTION =
  '对一篇或多篇外文 PDF 学术文献执行提取、中文翻译、图片提取与解读，返回结构化输出文件清单。' +
  '翻译/视觉的 API Key 已在插件配置中提供，不需要（也不应该）在参数里传 Key。' +
  '适合用户发来 PDF 文献并要求阅读、翻译、分析、总结或批量处理时使用。'

/** 手写参数 JSON Schema（原生 JSON Schema 子集，`required` 为顶层数组）。 */
const PARAMETERS = {
  type: 'object',
  properties: {
    pdfs: {
      type: 'array',
      description: 'PDF 文件路径（绝对路径，或相对插件 cwd 的路径）；支持多个、可含 * ? [ 通配符。',
      items: { type: 'string' },
    },
    formats: {
      type: 'string',
      description: '输出格式，逗号分隔：md(中英对照)/zh(纯中文)/html(网页)。默认取插件配置。',
    },
    out_dir: { type: 'string', description: '输出目录；默认取插件配置。' },
    no_translate: { type: 'boolean', description: '只转换提取，不调用翻译 API。' },
    vision: {
      type: 'boolean',
      description:
        '用外部视觉模型解读图片（OpenAI 兼容，如 gpt-4o/Qwen-VL/GLM-4V）；需 config.json 已配置 vision 段。DeepSeek 文本模型本身不能看图。',
    },
    vision_only: { type: 'boolean', description: '只提取并解读图片，不转换/翻译正文。' },
    no_figures: { type: 'boolean', description: '关闭图片提取。' },
    chunk_tokens: { type: 'integer', description: '每块最大 token。' },
    config: { type: 'string', description: 'JSON 配置文件路径；默认取插件配置的 configPath。' },
  },
  required: ['pdfs'],
}

/** 手写输出 JSON Schema（原生 JSON Schema 子集）。 */
const OUTPUT_SCHEMA: JsonSchemaNode = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ok: { type: 'boolean' },
    documents: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          pdf: { type: 'string' },
          type: { type: 'string' },
          pages: { type: 'integer' },
          chars: { type: 'integer' },
          chunks: { type: 'integer' },
          figures: { type: 'integer' },
          outputs: { type: 'object' },
        },
        required: ['pdf', 'type', 'pages', 'chars', 'chunks', 'figures', 'outputs'],
      },
    },
    failures: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          pdf: { type: 'string' },
          error: { type: 'string' },
        },
        required: ['pdf', 'error'],
      },
    },
  },
  required: ['ok', 'documents', 'failures'],
}

function renderResult(_args: unknown, value: unknown) {
  const v = value as RunResult
  const lines: string[] = []
  for (const d of v.documents) {
    lines.push(`${d.pdf}: ${d.type} / ${d.pages}页 / ${d.chunks}块 / ${d.figures}图`)
    for (const [kind, p] of Object.entries(d.outputs)) {
      lines.push(`  [${kind}] ${p}`)
    }
  }
  for (const f of v.failures) {
    lines.push(`${f.pdf}: 失败 / ${f.error}`)
  }
  if (lines.length === 0) lines.push('没有可处理的 PDF。')
  return [{ type: 'text' as const, text: lines.join('\n') }]
}

export function apply(ctx: Context, config: Config): void {
  const cwd = config.cwd ? path.resolve(config.cwd) : DEFAULT_REPO_ROOT

  const definition: ToolDefinition = {
    name: 'pdfreader',
    description: DESCRIPTION,
    parameters: PARAMETERS,
    output: {
      schema: OUTPUT_SCHEMA,
      render: renderResult,
    },
    timeoutMs: config.timeoutMs,
    isConcurrencySafe: () => false,
    async execute(args, exec) {
      const a = parseArgs(args)
      const outDirAbs = path.resolve(cwd, a.out_dir ?? config.outDir)
      const jsonPath = path.join(outDirAbs, `.pdfreader-result-${randomUUID()}.json`)

      const pdfs = a.pdfs.map((p) => (path.isAbsolute(p) ? p : path.resolve(cwd, p)))

      const argv: string[] = [
        config.pythonBin,
        '-m', 'pdfreader',
        ...pdfs,
        '--out-dir', outDirAbs,
        '--formats', a.formats ?? config.defaultFormats,
        '--json-out', jsonPath,
      ]
      if (a.no_translate) argv.push('--no-translate')
      if (a.vision) argv.push('--vision')
      if (a.vision_only) argv.push('--vision-only')
      if (a.no_figures) argv.push('--no-figures')
      if (a.chunk_tokens != null) argv.push('--chunk-tokens', String(a.chunk_tokens))
      const explicitCfg = a.config ?? config.configPath
      const defaultCfg = path.join(cwd, '.agents', 'skills', 'pdfreader', 'config.json')
      const cfgPath = explicitCfg ? path.resolve(cwd, explicitCfg) : defaultCfg
      const cfgExists = await fs.stat(cfgPath).then(() => true, () => false)
      if (explicitCfg || cfgExists) argv.push('--config', cfgPath)

      const timeoutSignal = AbortSignal.timeout(config.timeoutMs)
      const signal = AbortSignal.any([exec.signal, timeoutSignal])

      const python = await ctx.subprocess.resolveExecutable(config.pythonBin, undefined, signal)

      const handle: SubprocessHandle = ctx.subprocess.spawn({
        argv: [python, ...argv.slice(1)],
        cwd,
        stdio: {
          stdin: 'ignore',
          stdout: { maxBytes: config.maxOutputBytes },
          stderr: { maxBytes: config.maxOutputBytes },
        },
        graceMs: 3000,
        signal,
      })

      const outcome = await handle.done

      // 优先读取 CLI 写入的权威 JSON 结果。
      let result: RunResult | undefined
      try {
        result = JSON.parse(await fs.readFile(jsonPath, 'utf8')) as RunResult
      } catch {
        result = undefined
      } finally {
        await fs.rm(jsonPath, { force: true }).catch(() => {})
      }

      if (result) return result

      // 没有 JSON：说明 CLI 未走到汇总阶段（配置错误 / 取消 / 超时 / 崩溃）。
      const stdout = handle.collected.stdout?.readFrom(0).text ?? ''
      const stderr = handle.collected.stderr?.readFrom(0).text ?? ''
      if (exec.signal.aborted) {
        throw new Error('pdfreader 调用被取消')
      }
      if (timeoutSignal.aborted) {
        throw new Error(`pdfreader 超时（>${config.timeoutMs}ms）`)
      }
      const detail = (stderr || stdout).trim().slice(-2000)
      throw new Error(`pdfreader 失败（exit ${outcome.exitCode ?? 'signal'}）: ${detail || '无输出'}`)
    },
    presentCall: (args: unknown) => {
      const raw = (args ?? {}) as { pdfs?: unknown }
      const pdfs = Array.isArray(raw.pdfs)
        ? raw.pdfs.filter((p): p is string => typeof p === 'string')
        : []
      return {
        card: 'terminal',
        title: pdfs.length ? `python -m pdfreader ${pdfs.join(' ')}` : 'python -m pdfreader',
        description: 'PDF 文献提取/翻译/图片解读',
        cwd,
      }
    },
  }

  ctx.tools.register(definition)
}
