#!/usr/bin/env node
/**
 * dsh-tool-pdfreader 一键安装脚本
 *
 * 把「安装插件 + 注册 cordis.patch.yml + 自检」合成一条命令。
 *
 * 用法：
 *   node tool-pdfreader/setup.mjs
 *     [--profile web]              # 目标 profile，默认 web
 *     [--dsh-bin <dsh 路径>]       # 显式指定 dsh 命令位置
 *     [--python-bin <python>]      # python 可执行文件，默认 python
 *     [--config <config.json>]     # 显式 config.json 路径（不填则自动推导）
 *     [--skip-verify]              # 跳过自检
 *     [--dry-run]                  # 只打印计划，不实际改动
 *
 * 安装策略（逐级降级）：
 *   1) 能找到 dsh（PATH / --dsh-bin / npx 缓存）→ 用 `dsh plugin add`（最规范）
 *   2) 找不到 dsh 或 pnpm → 纯 Node 手动「建符号链接 + 写 cordis.patch.yml」
 *   3) 写盘失败 → 打印人工步骤，exit 1
 *
 * 之所以能「不依赖 dsh/pnpm」：lib/index.js 是自包含 bundle（零 peer 依赖），
 * 所以“安装”本质就是建一个符号链接让 DSH 按名字找到插件 + 写一段注册配置。
 */
import { promises as fs } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import os from 'node:os'
import { spawnSync } from 'node:child_process'

const pluginDir = path.dirname(fileURLToPath(import.meta.url)) // .../tool-pdfreader
const repoRoot = path.resolve(pluginDir, '..') // .../pdf-reader
const PLUGIN_NAME = 'dsh-tool-pdfreader'

// ============ 日志 ============
const log = (m) => console.log(m)
const warn = (m) => console.warn('[warn]', m)
const die = (m) => { console.error('[error]', m); process.exit(1) }

// ============ 参数 ============
function printHelp() {
  log(`用法: node ${path.basename(fileURLToPath(import.meta.url))} [选项]
  --profile <name>      目标 profile（默认 web）
  --dsh-bin <path>      显式指定 dsh 命令位置
  --python-bin <path>   python 可执行文件（默认 python）
  --config <path>       config.json 路径（不填则自动推导）
  --skip-verify         跳过自检
  --dry-run             只打印计划，不实际改动
  -h, --help            显示帮助`)
}

function parseArgs(argv) {
  const a = { profile: 'web', dshBin: '', pythonBin: 'python', config: '', skipVerify: false, dryRun: false }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--profile') a.profile = argv[++i]
    else if (k === '--dsh-bin') a.dshBin = argv[++i]
    else if (k === '--python-bin') a.pythonBin = argv[++i]
    else if (k === '--config') a.config = argv[++i]
    else if (k === '--skip-verify') a.skipVerify = true
    else if (k === '--dry-run') a.dryRun = true
    else if (k === '-h' || k === '--help') { printHelp(); process.exit(0) }
    else die(`未知参数: ${k}`)
  }
  return a
}

const args = parseArgs(process.argv.slice(2))

// ============ 路径 ============
const dshHome = process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
const profileDir = path.join(dshHome, 'profiles', args.profile)
const patchPath = path.join(profileDir, 'cordis.patch.yml')

const exists = (p) => fs.access(p).then(() => true, () => false)

// ============ 1. 定位 dsh ============
async function findDsh(explicit) {
  const candidates = []
  if (explicit) candidates.push(explicit)
  // PATH 里找 dsh（Windows 上 cmd 只认 .cmd/.exe，扩展名 shim 是给 POSIX 的）
  const exeNames = process.platform === 'win32' ? ['dsh.cmd', 'dsh.exe'] : ['dsh']
  for (const dir of (process.env.PATH || '').split(path.delimiter).filter(Boolean)) {
    for (const name of exeNames) candidates.push(path.join(dir, name))
  }
  // npx 缓存里递归找 dsh.cmd
  const cacheRoots = [
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'npm-cache', '_npx'),
    path.join(os.homedir(), 'AppData', 'Local', 'npm-cache', '_npx'),
  ].filter(Boolean)
  for (const root of cacheRoots) {
    if (!(await exists(root))) continue
    const found = await findInTree(root, 'dsh.cmd', 6)
    if (found) candidates.push(found)
  }
  for (const c of candidates) {
    if (c && (await exists(c))) return c
  }
  return null
}

async function findInTree(dir, name, depth) {
  if (depth < 0) return null
  let entries
  try { entries = await fs.readdir(dir, { withFileTypes: true }) } catch { return null }
  for (const e of entries) {
    const full = path.join(dir, e.name)
    if (e.isFile() && e.name === name) return full
    if (e.isDirectory() && !e.name.startsWith('.')) {
      const hit = await findInTree(full, name, depth - 1)
      if (hit) return hit
    }
  }
  return null
}

// ============ 2. 写 cordis.patch.yml（幂等） ============
function buildInsertBlock() {
  const configLines = [`        pythonBin: ${args.pythonBin}`]
  if (args.config) configLines.push(`        configPath: ${args.config.replace(/\\/g, '/')}`)
  configLines.push('        defaultFormats: md,zh,html', '        outDir: output', '        timeoutMs: 600000')
  return [
    '- insert:',
    '    - id: tool-pdfreader',
    `      name: '${PLUGIN_NAME}'`,
    '      config:',
    ...configLines,
    '',
  ].join('\n')
}

async function ensurePatchEntry() {
  let content = '[]'
  if (await exists(patchPath)) content = await fs.readFile(patchPath, 'utf8')
  if (content.includes('id: tool-pdfreader')) {
    log('✓ cordis.patch.yml 已存在 tool-pdfreader 条目，跳过写入')
    return false
  }
  const block = buildInsertBlock()
  const trimmed = content.trim()
  const next = (trimmed === '[]' || trimmed === '') ? block : content.replace(/\s*$/, '\n') + block
  await fs.writeFile(patchPath, next, 'utf8')
  log(`✓ 已写入 ${patchPath}`)
  return true
}

// ============ 3. 安装 ============
async function installViaDsh(dshBin) {
  log(`→ 用 dsh 安装：${dshBin} plugin --profile ${args.profile} add ${pluginDir}`)
  const r = spawnSync(dshBin, ['plugin', '--profile', args.profile, 'add', `"${pluginDir}"`],
    { shell: true, stdio: 'inherit' })
  return (r.status ?? 1) === 0
}

async function installManual() {
  // 确定 node_modules 位置：优先 hoisted profiles/node_modules，否则 profile 自己的
  const hoisted = path.join(dshHome, 'profiles', 'node_modules')
  const local = path.join(profileDir, 'node_modules')
  const nmDir = (await exists(hoisted)) ? hoisted : local

  // ① 建符号链接
  const linkPath = path.join(nmDir, PLUGIN_NAME)
  const type = process.platform === 'win32' ? 'junction' : 'dir'
  try {
    await fs.mkdir(nmDir, { recursive: true })
    try { await fs.unlink(linkPath) } catch {}
    await fs.symlink(pluginDir, linkPath, type)
    log(`✓ 已建符号链接 ${linkPath} → ${pluginDir}`)
  } catch (e) {
    warn(`符号链接创建失败（${e.message}），继续写配置`)
  }

  // ② 写 cordis.patch.yml（幂等）
  await ensurePatchEntry()

  // ③ 记录依赖到 package.json（尽量，失败不阻断）
  await recordPackageDep(nmDir)
  return true
}

async function recordPackageDep(nmDir) {
  const pkgPath = path.join(profileDir, 'package.json')
  if (!(await exists(pkgPath))) return
  try {
    const pkg = JSON.parse(await fs.readFile(pkgPath, 'utf8'))
    pkg.dependencies = pkg.dependencies || {}
    if (!pkg.dependencies[PLUGIN_NAME]) {
      const rel = path.relative(profileDir, pluginDir).replace(/\\/g, '/')
      pkg.dependencies[PLUGIN_NAME] = `link:${rel}`
      await fs.writeFile(pkgPath, JSON.stringify(pkg, null, 2) + '\n', 'utf8')
      log(`✓ 已记录依赖到 ${pkgPath}`)
    }
  } catch (e) {
    warn(`package.json 写入失败（${e.message}），不影响运行`)
  }
}

// ============ 4. 自检 ============
async function verify() {
  let ok = true

  // Python 侧：复用 --doctor
  log('\n== 自检 1/2：Python 侧 ==')
  const dr = spawnSync(args.pythonBin, ['-m', 'pdfreader', '--doctor'], { cwd: repoRoot, stdio: 'inherit' })
  ok &&= (dr.status ?? 1) === 0

  // Node 侧：在 profile 目录下 import 插件 + apply 冒烟
  log('\n== 自检 2/2：插件加载 ==')
  const hoisted = path.join(dshHome, 'profiles', 'node_modules')
  const local = path.join(profileDir, 'node_modules')
  const nmDir = (await exists(hoisted)) ? hoisted : local
  const runDir = path.dirname(nmDir) // 含 node_modules 的那层目录
  const probe = `import * as m from '${PLUGIN_NAME}'
const reg = []
m.apply({ tools: { register: (d) => { reg.push(d); return () => {} } }, subprocess: {} }, m.Config({}))
const d = reg[0]
if (!d || d.name !== 'pdfreader') { console.error('FAIL: 未注册出 pdfreader 工具'); process.exit(1) }
console.log('OK: 插件加载并注册成功，工具 =', d.name, '| cwd 自动推导 =', d.presentCall?.({})?.cwd)
`
  const probePath = path.join(runDir, `.dsh-pdfreader-probe-${process.pid}.mjs`)
  try {
    await fs.writeFile(probePath, probe, 'utf8')
    const r = spawnSync(process.execPath, [probePath], { cwd: runDir, stdio: 'inherit' })
    ok &&= (r.status ?? 1) === 0
  } catch (e) {
    warn(`插件加载自检失败：${e.message}`)
    ok = false
  } finally {
    await fs.rm(probePath, { force: true }).catch(() => {})
  }

  return ok
}

// ============ 人工兜底说明 ============
function printManualSteps() {
  warn(`\n自动安装失败。请按以下步骤手工完成：`)
  warn(`1) 建符号链接（Windows 用 junction）：`)
  warn(`   mklink /J "${path.join(dshHome, 'profiles', 'node_modules', PLUGIN_NAME)}" "${pluginDir}"`)
  warn(`2) 把下面内容并入 ${patchPath}：`)
  warn(buildInsertBlock())
  warn(`3) 重启 DSH`)
}

// ============ main ============
async function main() {
  log(`DSH_HOME: ${dshHome}`)
  log(`目标 profile: ${args.profile}`)
  log(`插件目录: ${pluginDir}`)
  log(`仓库根: ${repoRoot}`)

  const dshBin = await findDsh(args.dshBin)
  log(dshBin ? `dsh 位置: ${dshBin}` : '未找到 dsh 命令')

  if (args.dryRun) {
    log('\n[dry-run] 计划：')
    if (dshBin) log(`  · 用 dsh 安装: ${dshBin} plugin --profile ${args.profile} add "${pluginDir}"`)
    else log('  · 用纯 Node 手动安装：建符号链接 + 写 cordis.patch.yml')
    log('  · 注册 cordis.patch.yml（幂等）')
    if (!args.skipVerify) log('  · 自检：python --doctor + 插件加载冒烟')
    log('\n[dry-run] 未做任何改动。去掉 --dry-run 实际执行。')
    return 0
  }

  let installed = false
  if (dshBin) {
    installed = await installViaDsh(dshBin)
    if (!installed) warn('dsh plugin add 失败（可能缺 pnpm），降级为手动安装')
  }
  if (!installed) {
    installed = await installManual()
  }
  if (!installed) {
    printManualSteps()
    die('安装失败')
  }

  if (!args.skipVerify) {
    const ok = await verify()
    if (!ok) {
      warn('\n自检未通过：请根据上面的报错修复后重试。')
      process.exit(1)
    }
  }

  log('\n✓ 安装完成。重启 DSH 后，模型即可调用 `pdfreader` 工具。')
  log('  验证工具是否可见：重启后让模型调用 pdfreader，或查看 tools 目录。')
  return 0
}

main().catch((e) => die(e.stack || e.message))
