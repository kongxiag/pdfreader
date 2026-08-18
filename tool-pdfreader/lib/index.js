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
 */
import { promises as fs } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { defineTool } from '@deepseek-ai/dsh-tools';
import z from '@deepseek-ai/schemastery';
export const name = 'tool-pdfreader';
export const inject = ['tools', 'subprocess'];
export const Config = z.object({
    cwd: z.string().required(),
    pythonBin: z.string().default('python'),
    configPath: z.string().default(''),
    defaultFormats: z.string().default('md,zh,html'),
    outDir: z.string().default('output'),
    timeoutMs: z.number().default(600000),
    maxOutputBytes: z.number().default(64000),
});
const DESCRIPTION = '对一篇或多篇外文 PDF 学术文献执行提取、中文翻译、图片提取与解读，返回结构化输出文件清单。' +
    '翻译/视觉的 API Key 已在插件配置中提供，不需要（也不应该）在参数里传 Key。' +
    '适合用户发来 PDF 文献并要求阅读、翻译、分析、总结或批量处理时使用。';
const OUTPUT_SCHEMA = {
    type: 'object',
    additionalProperties: false,
    properties: {
        ok: { type: 'boolean', required: true },
        documents: {
            type: 'array',
            required: true,
            items: {
                type: 'object',
                additionalProperties: false,
                properties: {
                    pdf: { type: 'string', required: true },
                    type: { type: 'string', required: true },
                    pages: { type: 'integer', required: true },
                    chars: { type: 'integer', required: true },
                    chunks: { type: 'integer', required: true },
                    figures: { type: 'integer', required: true },
                    outputs: { type: 'object', additionalProperties: true, required: true },
                },
            },
        },
        failures: {
            type: 'array',
            required: true,
            items: {
                type: 'object',
                additionalProperties: false,
                properties: {
                    pdf: { type: 'string', required: true },
                    error: { type: 'string', required: true },
                },
            },
        },
    },
};
function renderResult(_args, value) {
    const lines = [];
    for (const d of value.documents) {
        lines.push(`${d.pdf}: ${d.type} / ${d.pages}页 / ${d.chunks}块 / ${d.figures}图`);
        for (const [kind, p] of Object.entries(d.outputs)) {
            lines.push(`  [${kind}] ${p}`);
        }
    }
    for (const f of value.failures) {
        lines.push(`${f.pdf}: 失败 / ${f.error}`);
    }
    if (lines.length === 0)
        lines.push('没有可处理的 PDF。');
    return [{ type: 'text', text: lines.join('\n') }];
}
export function apply(ctx, config) {
    const cwd = path.resolve(config.cwd);
    ctx.tools.register(defineTool({
        name: 'pdfreader',
        description: DESCRIPTION,
        parameters: {
            pdfs: {
                type: 'array',
                required: true,
                description: 'PDF 文件路径（绝对路径，或相对插件 cwd 的路径）；支持多个、可含 * ? [ 通配符。',
                items: { type: 'string' },
            },
            formats: {
                type: 'string',
                description: '输出格式，逗号分隔：md(中英对照)/zh(纯中文)/html(网页)。默认取插件配置。',
            },
            out_dir: { type: 'string', description: '输出目录；默认取插件配置。' },
            no_translate: { type: 'boolean', description: '只转换提取，不调用翻译 API。' },
            vision: { type: 'boolean', description: '用外部视觉模型解读图片（OpenAI 兼容，如 gpt-4o/Qwen-VL/GLM-4V）；需 config.json 已配置 vision 段。DeepSeek 文本模型本身不能看图。' },
            vision_only: { type: 'boolean', description: '只提取并解读图片，不转换/翻译正文。' },
            no_figures: { type: 'boolean', description: '关闭图片提取。' },
            chunk_tokens: { type: 'integer', description: '每块最大 token。' },
            config: { type: 'string', description: 'JSON 配置文件路径；默认取插件配置的 configPath。' },
        },
        output: {
            schema: OUTPUT_SCHEMA,
            render: renderResult,
        },
        timeoutMs: config.timeoutMs,
        isConcurrencySafe: () => false,
        async execute(args, exec) {
            const outDirAbs = path.resolve(cwd, args.out_dir ?? config.outDir);
            const jsonPath = path.join(outDirAbs, `.pdfreader-result-${randomUUID()}.json`);
            const pdfs = args.pdfs.map((p) => (path.isAbsolute(p) ? p : path.resolve(cwd, p)));
            const argv = [
                config.pythonBin,
                '-m', 'pdfreader',
                ...pdfs,
                '--out-dir', outDirAbs,
                '--formats', args.formats ?? config.defaultFormats,
                '--json-out', jsonPath,
            ];
            if (args.no_translate)
                argv.push('--no-translate');
            if (args.vision)
                argv.push('--vision');
            if (args.vision_only)
                argv.push('--vision-only');
            if (args.no_figures)
                argv.push('--no-figures');
            if (args.chunk_tokens != null)
                argv.push('--chunk-tokens', String(args.chunk_tokens));
            const cfgPath = args.config ?? config.configPath;
            if (cfgPath)
                argv.push('--config', path.resolve(cwd, cfgPath));
            const timeoutSignal = AbortSignal.timeout(config.timeoutMs);
            const signal = AbortSignal.any([exec.signal, timeoutSignal]);
            const python = await ctx.subprocess.resolveExecutable(config.pythonBin, undefined, signal);
            const handle = ctx.subprocess.spawn({
                argv: [python, ...argv.slice(1)],
                cwd,
                stdio: {
                    stdin: 'ignore',
                    stdout: { maxBytes: config.maxOutputBytes },
                    stderr: { maxBytes: config.maxOutputBytes },
                },
                graceMs: 3000,
                signal,
            });
            const outcome = await handle.done;
            // 优先读取 CLI 写入的权威 JSON 结果。
            let result;
            try {
                result = JSON.parse(await fs.readFile(jsonPath, 'utf8'));
            }
            catch {
                result = undefined;
            }
            finally {
                await fs.rm(jsonPath, { force: true }).catch(() => { });
            }
            if (result)
                return result;
            // 没有 JSON：说明 CLI 未走到汇总阶段（配置错误 / 取消 / 超时 / 崩溃）。
            const stdout = handle.collected.stdout?.readFrom(0).text ?? '';
            const stderr = handle.collected.stderr?.readFrom(0).text ?? '';
            if (exec.signal.aborted) {
                throw new Error('pdfreader 调用被取消');
            }
            if (timeoutSignal.aborted) {
                throw new Error(`pdfreader 超时（>${config.timeoutMs}ms）`);
            }
            const detail = (stderr || stdout).trim().slice(-2000);
            throw new Error(`pdfreader 失败（exit ${outcome.exitCode ?? 'signal'}）: ${detail || '无输出'}`);
        },
        presentCall: (args) => ({
            card: 'terminal',
            title: `python -m pdfreader ${args.pdfs.join(' ')}`,
            description: 'PDF 文献提取/翻译/图片解读',
            cwd,
        }),
    }));
}
