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
      '--mcp-config', '/app/mcp-config.json',
      '--strict-mcp-config',
    ]

    if (model) {
      args.push('--model', model)
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
    args.push('--allowedTools', MOVIEWEB_TOOLS)

    // Seed ephemeral home with baked-in settings so permissions apply cleanly
    try {
      const fs = require('fs')
      fs.mkdirSync(`${ephemeralHome}/.claude`, { recursive: true })
      fs.copyFileSync('/home/claude/.claude/settings.json', `${ephemeralHome}/.claude/settings.json`)
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

    proc.on('close', (code) => {
      clearTimeout(timer)
      cleanup()
      resolve({
        stdout,
        stderr,
        exit_code: timedOut ? -1 : (code ?? -1),
        timed_out: timedOut,
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
    return send(res, 200, { status: 'ok', claude_available: claudeAvailable() })
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
  console.log(JSON.stringify({ event: 'server_start', port: PORT }))
  console.log(JSON.stringify({ event: 'claude_check', available: claudeAvailable() }))
})
