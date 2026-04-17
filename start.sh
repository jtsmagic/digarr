#!/bin/sh
# Defaults — override by passing -e PORT=... or -e UVICORN_PORT=... to docker run
PORT=${PORT:-8090}
UVICORN_PORT=${UVICORN_PORT:-8091}

export PORT UVICORN_PORT

# Materialise nginx config with actual port values and enable it
envsubst '${PORT} ${UVICORN_PORT}' \
    < /etc/nginx/sites-available/default.template \
    > /etc/nginx/sites-enabled/default

# Start nginx in background
nginx -g "daemon off;" &

# Start FastAPI backend
exec uvicorn main:app --host 127.0.0.1 --port "${UVICORN_PORT}" --workers "${DIGARR_WORKERS:-2}"
