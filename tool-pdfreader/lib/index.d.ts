import type { Context } from '@deepseek-ai/cordis';
import z from '@deepseek-ai/schemastery';
export declare const name = "tool-pdfreader";
export declare const inject: string[];
/** 插件配置：在 cordis.patch.yml / profile 中提供。 */
export interface Config {
    /** pdf-reader 项目目录（`python -m pdfreader` 的运行目录），绝对路径。 */
    cwd: string;
    /** Python 可执行文件：裸名走 PATH，或绝对路径。 */
    pythonBin: string;
    /** 默认 config.json 绝对路径；空串表示不传 --config。 */
    configPath: string;
    /** 默认输出格式（md/zh/html 逗号分隔）。 */
    defaultFormats: string;
    /** 输出目录（相对 cwd 或绝对路径）。 */
    outDir: string;
    /** 单次调用的协作超时预算（ms）。 */
    timeoutMs: number;
    /** stdout/stderr 收集的内存上限（字节），仅用于诊断展示。 */
    maxOutputBytes: number;
}
export declare const Config: z<Schemastery.ObjectS<{
    cwd: z<string, string>;
    pythonBin: z<string, string>;
    configPath: z<string, string>;
    defaultFormats: z<string, string>;
    outDir: z<string, string>;
    timeoutMs: z<number, number>;
    maxOutputBytes: z<number, number>;
}>, Schemastery.ObjectT<{
    cwd: z<string, string>;
    pythonBin: z<string, string>;
    configPath: z<string, string>;
    defaultFormats: z<string, string>;
    outDir: z<string, string>;
    timeoutMs: z<number, number>;
    maxOutputBytes: z<number, number>;
}>>;
export declare function apply(ctx: Context, config: Config): void;
