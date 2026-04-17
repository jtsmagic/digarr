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
RUN apt-get update && apt-get install -y nginx gettext-base && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./

# Copy built frontend
COPY --from=frontend-build /app/frontend/build /var/www/digarr

# Copy nginx config as a template; start.sh materialises it with envsubst
COPY nginx.conf /etc/nginx/sites-available/default.template
RUN rm -f /etc/nginx/sites-enabled/default

# Create data directory
RUN mkdir -p /data

# Startup script
COPY start.sh ./
RUN chmod +x start.sh

# Default container port — override at runtime with -e PORT=XXXX -p XXXX:XXXX
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8090}/ || exit 1

CMD ["./start.sh"]

