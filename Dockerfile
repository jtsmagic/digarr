# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN REACT_APP_VERSION=$(node -p "require('./package.json').version") npm run build

# Stage 2: Python backend + serve frontend via nginx
FROM python:3.12-slim AS backend

WORKDIR /app

# Install nginx + envsubst (gettext-base) for port-templating
RUN apt-get update && apt-get install -y nginx gettext-base gosu && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy built frontend
COPY --from=frontend-build /app/frontend/build /var/www/digarr

# Copy nginx configs
COPY nginx-main.conf /etc/nginx/nginx.conf
COPY nginx.conf /etc/nginx/sites-available/default.template
RUN rm -f /etc/nginx/sites-enabled/default

# Create data directory and nginx temp dirs
RUN mkdir -p /data \
             /tmp/nginx-client-body /tmp/nginx-proxy /tmp/nginx-fastcgi \
             /tmp/nginx-scgi /tmp/nginx-uwsgi

# Create non-root user and give it ownership of everything it needs to write
RUN groupadd -r digarr && useradd -r -g digarr -s /bin/false digarr && \
    chown -R digarr:digarr /app /data /var/www/digarr \
        /etc/nginx/sites-available /etc/nginx/sites-enabled \
        /var/log/nginx /tmp/nginx-client-body /tmp/nginx-proxy \
        /tmp/nginx-fastcgi /tmp/nginx-scgi /tmp/nginx-uwsgi

# Entrypoint (runs as root to fix /data ownership, then drops to digarr)
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Startup script (runs as digarr after entrypoint drops privileges)
COPY start.sh ./
RUN chmod +x start.sh

ENTRYPOINT ["./entrypoint.sh"]

# Default container port — override at runtime with -e PORT=XXXX -p XXXX:XXXX
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8090}/ || exit 1

CMD ["./start.sh"]

