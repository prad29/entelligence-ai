FROM public.ecr.aws/docker/library/node:22-slim

# Claude Code auto-updater tries to write to the install path in containers — disable it
ENV DISABLE_AUTOUPDATER=1
ENV CLAUDE_NO_AUTO_UPDATE=1

RUN npm install -g @anthropic-ai/claude-code

# Create a non-root user — Claude refuses --dangerously-skip-permissions as root
RUN useradd -m -u 1001 -s /bin/bash claude

# Bake minimal Claude settings for the claude user
RUN mkdir -p /home/claude/.claude && \
    cat > /home/claude/.claude/settings.json <<'EOF'
{
  "permissions": {
    "allow": ["WebFetch(*)", "WebSearch(*)", "mcp__movieweb__web_search", "mcp__movieweb__web_fetch"],
    "deny": []
  }
}
EOF
RUN chown -R claude:claude /home/claude/.claude

WORKDIR /app
COPY package.json .
RUN npm install
COPY server.js .

# movieweb MCP server (web_search/web_fetch via Serper — Bedrock has no
# built-in WebSearch tool, so this is baked in as a custom MCP tool instead).
COPY mcp-servers/movieweb/package.json mcp-servers/movieweb/package.json
RUN cd mcp-servers/movieweb && npm install --omit=dev
COPY mcp-servers/movieweb/server.js mcp-servers/movieweb/server.js
COPY mcp-config.json mcp-config.json

RUN chown -R claude:claude /app

# Switch to non-root for runtime
USER claude

EXPOSE 3100

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=30s \
  CMD node -e "require('http').get('http://localhost:3100/health', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
