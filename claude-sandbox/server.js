/**
 * claude-sandbox: thin HTTP wrapper around the Claude Code CLI.
 *
 * POST /run  { prompt: string, model: string, tools: string, timeout_seconds: number }
 *   → { stdout: string, stderr: string, exit_code: number }
 *
 * GET /health → 200 { status: "ok", claude_available: bool }
 */

'use strict'

const http = require('http')
const { spawn } = require('child_process')
const { execSync } = require('child_process')

const PORT = parseInt(process.env.CLAUDE_SANDBOX_PORT || '3100', 10)
const DEFAULT_TIMEOUT_MS = parseInt(process.env.CLAUDE_DEFAULT_TIMEOUT_MS || '90000', 10)
const MAX_TIMEOUT_MS = 300_000

// Market-profile knobs -- same image for both claude-sandbox and
// claude-sandbox-intl, differentiated purely by env so the `claude` CLI
// version and the movieweb MCP server can never drift between markets.
// Every default below reproduces today's domestic behavior byte-for-byte
// when unset.
const SANDBOX_PROFILE = process.env.SANDBOX_PROFILE || 'domestic'
const MCP_CONFIG_PATH = process.env.MCP_CONFIG_PATH || '/app/mcp-config.json'
const SETTINGS_PATH = process.env.SETTINGS_PATH || '/home/claude/.claude/settings.json'
const DEFAULT_MODEL = process.env.CLAUDE_DEFAULT_MODEL || ''
const EXTRA_ALLOWED_TOOLS = process.env.EXTRA_ALLOWED_TOOLS || ''

function claudeAvailable() {
  try {
    execSync('which claude', { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

function runClaude({ prompt, model, tools, timeoutMs }) {
  return new Promise((resolve) => {
    // Unique ephemeral home dir per request — prevents session context/compaction
    // leaking between calls. Cleaned up after the process exits.
    const reqId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const ephemeralHome = `/tmp/claude-run-${reqId}`

    const env = {
      ...process.env,
      DISABLE_AUTOUPDATER: '1',
      CLAUDE_NO_AUTO_UPDATE: '1',
      HOME: ephemeralHome,
      CLAUDE_CONFIG_DIR: `${ephemeralHome}/.claude`,
    }

    const args = [
      '--print', '-',
      '--output-format', 'stream-json',
      '--verbose',
      '--no-session-persistence',
      '--dangerously-skip-permissions',
      // Static MCP config baked into the image — connects the movieweb
      // (Serper-backed web_search/web_fetch) server with alwaysLoad:true so
      // it's connected before the first prompt, no interactive approval
      // needed (--strict-mcp-config bypasses the .mcp.json approval gate
      // entirely, which otherwise leaves a server stuck "pending" forever
      // in a one-shot --print session).
      '--mcp-config', MCP_CONFIG_PATH,
      '--strict-mcp-config',
    ]

    // Container-level model default, independent of the request body -- lets
    // a sandbox profile pin a model without every caller having to pass one.
    // A caller-supplied `model` still wins.
    const effectiveModel = model || DEFAULT_MODEL
    if (effectiveModel) {
      args.push('--model', effectiveModel)
    }

    // --tools only restricts/enables built-in tools and does not affect MCP
    // tools, so the movieweb tools are always appended via --allowedTools
    // regardless of what the caller requested for built-ins. `tools` may be
    // "" (disable all built-ins, per the CLI's own semantics) — that's a
    // real, distinct value from "not specified", so check for undefined/null
    // rather than truthiness.
    const MOVIEWEB_TOOLS = 'mcp__movieweb__web_search,mcp__movieweb__web_fetch'
    if (tools !== undefined && tools !== null) {
      args.push('--tools', tools)
    }
    const allowedTools = EXTRA_ALLOWED_TOOLS
      ? `${MOVIEWEB_TOOLS},${EXTRA_ALLOWED_TOOLS}`
      : MOVIEWEB_TOOLS
    args.push('--allowedTools', allowedTools)

    // Seed ephemeral home with baked-in settings so permissions apply cleanly
    try {
      const fs = require('fs')
      fs.mkdirSync(`${ephemeralHome}/.claude`, { recursive: true })
      fs.copyFileSync(SETTINGS_PATH, `${ephemeralHome}/.claude/settings.json`)
    } catch {}

    const proc = spawn('claude', args, {
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })

    let stdout = ''
    let stderr = ''
    let timedOut = false

    const timer = setTimeout(() => {
      timedOut = true
      proc.kill('SIGKILL')
    }, Math.min(timeoutMs, MAX_TIMEOUT_MS))

    proc.stdout.on('data', (chunk) => { stdout += chunk.toString() })
    proc.stderr.on('data', (chunk) => { stderr += chunk.toString() })

    proc.stdin.write(prompt)
    proc.stdin.end()

    const cleanup = () => {
      try { require('fs').rmSync(ephemeralHome, { recursive: true, force: true }) } catch {}
    }

    // The movieweb MCP server (spawned by `claude` as a child process, and
    // inheriting this same ephemeralHome as its $HOME) appends one JSON line
    // per web_search/web_fetch call to this file. Read before cleanup() deletes
    // the ephemeral home dir; missing/unreadable/malformed is a clean [], never
    // a thrown error (spec §7) — a run with no Serper calls has no such file.
    function readSerperCalls() {
      try {
        const fs = require('fs')
        const raw = fs.readFileSync(`${ephemeralHome}/serper-calls.jsonl`, 'utf8')
        return raw
          .split('\n')
          .filter((line) => line.trim())
          .map((line) => {
            try {
              return JSON.parse(line)
            } catch {
              return null
            }
          })
          .filter(Boolean)
      } catch {
        return []
      }
    }

    proc.on('close', (code) => {
      clearTimeout(timer)
      const serperCalls = readSerperCalls()
      cleanup()
      resolve({
        stdout,
        stderr,
        exit_code: timedOut ? -1 : (code ?? -1),
        timed_out: timedOut,
        serper_calls: serperCalls,
      })
    })

    proc.on('error', (err) => {
      clearTimeout(timer)
      cleanup()
      resolve({
        stdout: '',
        stderr: err.message,
        exit_code: -1,
        timed_out: false,
        serper_calls: [],
      })
    })
  })
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString()))
      } catch (e) {
        reject(new Error('Invalid JSON body'))
      }
    })
    req.on('error', reject)
  })
}

function send(res, status, body) {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload),
  })
  res.end(payload)
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return send(res, 200, { status: 'ok', claude_available: claudeAvailable(), profile: SANDBOX_PROFILE })
  }

  if (req.method === 'POST' && req.url === '/run') {
    let body
    try {
      body = await readBody(req)
    } catch (e) {
      return send(res, 400, { error: e.message })
    }

    const { prompt, model, tools, timeout_seconds } = body

    if (!prompt || typeof prompt !== 'string') {
      return send(res, 400, { error: 'prompt is required and must be a string' })
    }

    const timeoutMs = timeout_seconds ? timeout_seconds * 1000 : DEFAULT_TIMEOUT_MS

    console.log(JSON.stringify({
      event: 'run_start',
      profile: SANDBOX_PROFILE,
      model: model || 'default',
      tools: tools || 'none',
      timeout_ms: timeoutMs,
      prompt_length: prompt.length,
    }))

    const result = await runClaude({ prompt, model, tools, timeoutMs })

    console.log(JSON.stringify({
      event: 'run_end',
      exit_code: result.exit_code,
      timed_out: result.timed_out,
      stdout_length: result.stdout.length,
    }))

    return send(res, 200, result)
  }

  return send(res, 404, { error: 'Not found' })
})

server.listen(PORT, '0.0.0.0', () => {
  console.log(JSON.stringify({ event: 'server_start', port: PORT, profile: SANDBOX_PROFILE }))
  console.log(JSON.stringify({ event: 'claude_check', available: claudeAvailable() }))
})
