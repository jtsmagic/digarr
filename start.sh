#!/bin/sh
# Defaults — override by passing -e PORT=... or -e UVICORN_PORT=... to docker run
PORT=${PORT:-8090}
UVICORN_PORT=${UVICORN_PORT:-8091}
MCP_PORT=${MCP_PORT:-8092}

export PORT UVICORN_PORT MCP_PORT

# Materialise nginx config with actual port values and enable it
envsubst '${PORT} ${UVICORN_PORT} ${MCP_PORT}' \
    < /etc/nginx/sites-available/default.template \
    > /etc/nginx/sites-enabled/default

# Start nginx in background
nginx -g "daemon off;" &

# Optional remote MCP endpoint — only starts if configured (see mcp_server/http_server.py)
if [ -n "$DIGARR_MCP_ISSUER_URL" ] && [ -n "$DIGARR_MCP_PUBLIC_URL" ]; then
    python -m mcp_server.http_server &
fi

# Start FastAPI backend
# One worker by default. The app is I/O-bound (HTTP calls to Plex, Lidarr,
# Spotify) and asyncio already handles concurrent requests within a single
# process, so extra workers buy no throughput here - they only duplicate the
# background refresh worker and multiply SQLite write contention. Raise
# DIGARR_WORKERS if you actually front many concurrent users.
exec uvicorn main:app --host 127.0.0.1 --port "${UVICORN_PORT}" --workers "${DIGARR_WORKERS:-1}"
