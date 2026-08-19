# dsh-tool-pdfreader

> English · 中文版：[README.zh.md](README.zh.md)

A **DeepSeek Harness tool plugin** that wraps [`pdf-reader`](../) (Python) so the model can
call a `pdfreader` tool directly — running the
"PDF → classify → extract/OCR → chunk → Chinese translation → figure extraction/interpretation → report"
pipeline and returning a structured list of output files.

> Unlike the `skill` (an instruction-based skill), the plugin registers a **typed tool**:
> parameters have a schema, results are canonical JSON, and it participates in parallel /
> Code Mode / UI cards. A skill "teaches the model to run commands"; a plugin lets the model
> "call a function". Both share the same `config.json`.

## Prerequisites

1. Python 3.10+, with `pip install -r ../requirements.txt` (pdf-inspector, PyMuPDF, etc.).
2. A translation/vision `config.json` (shared with the skill; see
   `../.agents/skills/pdfreader/config.example.json`).
   - Figure **extraction** needs no vision model; figure **interpretation** needs an
     OpenAI-compatible vision model in `config.json` (e.g. gpt-4o / Qwen-VL / GLM-4V),
     because the DeepSeek text model cannot see images itself.

## Build

`lib/index.js` is a **self-contained bundle**: esbuild inlines the sole runtime dependency
(`schemastery`); the output depends only on Node built-ins
(`node:fs` / `node:crypto` / `node:path`) with **zero peer dependencies**. This fully avoids
the peer-resolution failure caused by Node's realpath when `dsh plugin add <local dir>` uses
a `link:` symlink (`@deepseek-ai/dsh-tools` etc. are `import type`-only, so they never enter
the output).

`lib/` is prebuilt and installable as-is. To rebuild:

```bash
cd tool-pdfreader
npm install          # esbuild / typescript / @types/node / @deepseek-ai/* (build-time only)
npm run build        # tsc type-check + declaration emit + esbuild bundle -> lib/
```

## Installing into DSH

### Option 1: one-command script (recommended)

```bash
node tool-pdfreader/setup.mjs            # install + register + preflight
node tool-pdfreader/setup.mjs --dry-run  # preview the plan without making changes
```

The script detects `dsh` and installs via `dsh plugin add` (falling back to a pure-Node
"symlink + cordis.patch.yml" when `dsh`/`pnpm` are absent), then runs a preflight
(Python `--doctor` + plugin-load smoke test) and reminds you to restart. Idempotent — safe to
re-run.

### Option 2: manual (three steps)

#### Step 1: install the package into the profile

```bash
dsh plugin --profile web add /path/to/pdf-reader/tool-pdfreader   # use your actual path
```

`dsh plugin` forwards to pnpm, which adds this directory as a dependency under
`<DSH_HOME>/profiles/<profile>/node_modules`. Because `lib/index.js` is a self-contained
bundle (no peer dependencies), there is no peer-resolution issue whether pnpm uses `link:`
or `file:`.

#### Step 2: register in `cordis.patch.yml`

Edit `<DSH_HOME>/profiles/<profile>/cordis.patch.yml` and append (or copy from
[`cordis.patch.example.yml`](./cordis.patch.example.yml)):

```yaml
- insert:
    - id: tool-pdfreader
      name: 'dsh-tool-pdfreader'
      config:
        # cwd / configPath can be omitted: cwd auto-resolves to the repo root, and
        # configPath auto-tries <repo-root>/.agents/skills/pdfreader/config.json.
        # Only set them explicitly for non-standard layouts.
        pythonBin: python
        defaultFormats: md,zh,html
        outDir: output
        timeoutMs: 600000
```

#### Step 3: restart DSH

After restart, the model can call the `pdfreader` tool.

> Web note: model-visible tools are composed by the **agent preset**; this plugin registers
> into the host-plane global tool registry (scope chain `agent → preset → global`), which is
> merged into every agent's tool view. For per-preset control, copy a user preset
> (`$DSH_HOME/.agent-presets/<id>/`) and reference this plugin by absolute path in its
> `agent.cordis.yml`.

## Configuration

| Field | Default | Description |
|---|---|---|
| `cwd` | auto | Working directory for `python -m pdfreader`; defaults to the repo root (set explicitly only for `file:`/npm installs or non-standard layouts) |
| `pythonBin` | `python` | Python executable (bare name on PATH, or an absolute path) |
| `configPath` | auto | Default `config.json`; empty auto-tries `<cwd>/.agents/skills/pdfreader/config.json` (passed only if it exists) |
| `defaultFormats` | `md,zh,html` | Default output formats |
| `outDir` | `output` | Output directory (relative to `cwd` or absolute) |
| `timeoutMs` | `600000` | Cooperative timeout budget (long papers can take minutes) |
| `maxOutputBytes` | `64000` | stdout/stderr capture cap (diagnostics only; results come via `--json-out`) |

## Tool parameters

| Parameter | Type | Description |
|---|---|---|
| `pdfs` | string[] (required) | PDF paths; multiple paths / globs supported |
| `formats` | string | `md,zh,html`, comma-separated |
| `out_dir` | string | Override the configured output directory |
| `no_translate` | boolean | Extract only, skip translation |
| `vision` | boolean | Interpret figures with an external vision model (requires vision config) |
| `vision_only` | boolean | Extract and interpret figures only |
| `no_figures` | boolean | Disable figure extraction |
| `chunk_tokens` | integer | Max tokens per chunk |
| `config` | string | Override the configured config.json path |

API keys are **not** tool parameters: they are always read from `config.json`, keeping them
out of call history and logs.

## Return value

Structured JSON (also rendered to model-readable text via `render`):

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

## Implementation notes & security

- Drives Python via `ctx.subprocess.spawn` with an **argv array** (no shell), avoiding
  quoting issues for Windows paths with spaces/CJK; `ctx.subprocess` handles managed process
  trees, bounded output, and cancellation escalation.
- Results do not come from parsing stdout: the CLI's `--json-out` writes an authoritative
  result to a temp JSON in the workspace, which the plugin reads and deletes.
- `exec.signal` is fused with `AbortSignal.timeout` and forwarded to the child process, so
  cancellation/timeout triggers process-tree termination.
- The plugin runs Python with the harness process's privileges (same trust level as the
  bash/pwsh tools); add `tools/pre-execute` or a sandbox executor in the profile if you need
  sandboxing/approval gates.
