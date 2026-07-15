/**
 * movieweb: stdio MCP server exposing web_search / web_fetch backed by Serper
 * (google.serper.dev), for use inside the claude-sandbox Bedrock container
 * where Anthropic's built-in WebSearch tool is unavailable.
 *
 * Both tools return clean, pre-shaped JSON (not raw Serper responses) so the
 * agent gets the same signal the built-in WebSearch/WebFetch tools would give.
 */

'use strict'

const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js')
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js')
const { z } = require('zod')

const SERPER_API_KEY = process.env.SERPER_API_KEY || ''
const SEARCH_URL = 'https://google.serper.dev/search'
const SCRAPE_URL = 'https://scrape.serper.dev'
const REQUEST_TIMEOUT_MS = 20_000
const MAX_RETRIES = 3
const RETRY_BASE_DELAY_MS = 1_000
const MAX_FETCH_TEXT_CHARS = 8_000

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function postJson(url, body) {
  if (!SERPER_API_KEY) {
    throw new Error('SERPER_API_KEY is not configured in the claude-sandbox environment')
  }

  let lastError
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'X-API-KEY': SERPER_API_KEY,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      if (response.status === 429 || response.status >= 500) {
        lastError = new Error(`Serper returned ${response.status}`)
      } else if (!response.ok) {
        const text = await response.text().catch(() => '')
        throw new Error(`Serper returned ${response.status}: ${text.slice(0, 200)}`)
      } else {
        return await response.json()
      }
    } catch (err) {
      lastError = err
    } finally {
      clearTimeout(timer)
    }

    if (attempt < MAX_RETRIES - 1) {
      await sleep(RETRY_BASE_DELAY_MS * 2 ** attempt)
    }
  }

  throw lastError
}

function toolError(message) {
  return { content: [{ type: 'text', text: `Error: ${message}` }], isError: true }
}

function toolText(payload) {
  return { content: [{ type: 'text', text: JSON.stringify(payload, null, 2) }] }
}

const server = new McpServer({ name: 'movieweb', version: '1.0.0' })

server.registerTool(
  'web_search',
  {
    title: 'Web search',
    description:
      'Search the web (Google, via Serper) to resolve or translate an obscure, foreign-language, ' +
      'or ambiguous movie/show/event title. Returns ranked results with title, link, and snippet. ' +
      'Use this when the pre-fetched DB/Vespa candidates are insufficient to confirm the input title.',
    inputSchema: {
      query: z.string().min(1).describe('The search query string'),
      num: z.number().int().min(1).max(20).optional().describe('Number of results to return (default 8)'),
    },
  },
  async ({ query, num }) => {
    try {
      const data = await postJson(SEARCH_URL, { q: query, num: num || 8 })
      const organic = Array.isArray(data.organic) ? data.organic : []
      const results = organic.slice(0, num || 8).map((r) => ({
        title: r.title || '',
        link: r.link || '',
        snippet: r.snippet || '',
      }))
      const payload = { results }
      if (data.answerBox) {
        payload.answerBox = {
          title: data.answerBox.title || '',
          link: data.answerBox.link || '',
          snippet: data.answerBox.snippet || '',
        }
      }
      return toolText(payload)
    } catch (err) {
      return toolError(err.message || String(err))
    }
  }
)

server.registerTool(
  'web_fetch',
  {
    title: 'Fetch web page',
    description:
      'Fetch a web page (e.g. a ticketing page, IMDb/Wikipedia page, or poster image page) and return ' +
      'its extracted text content, for confirming title/date/cast details. Use after web_search to ' +
      'read a specific result page.',
    inputSchema: {
      url: z.string().url().describe('The URL to fetch'),
    },
  },
  async ({ url }) => {
    try {
      const data = await postJson(SCRAPE_URL, { url })
      const text = typeof data.text === 'string' ? data.text : ''
      const truncated = text.length > MAX_FETCH_TEXT_CHARS
      return toolText({
        url,
        title: data.metadata?.title || '',
        text: text.slice(0, MAX_FETCH_TEXT_CHARS),
        truncated,
      })
    } catch (err) {
      return toolError(err.message || String(err))
    }
  }
)

async function main() {
  const transport = new StdioServerTransport()
  await server.connect(transport)
}

main().catch((err) => {
  process.stderr.write(`movieweb MCP server failed to start: ${err && err.stack ? err.stack : err}\n`)
  process.exit(1)
})
