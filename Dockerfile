# ── Stage 1: build the React frontend ────────────────────────────────────────
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python API + static serving ─────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install Python dependencies (locked, no dev extras)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Install the project itself
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Copy pre-built frontend assets so FastAPI can serve the SPA
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

EXPOSE 5001

# SQLite lives on a named volume; override via NIDOZO_DB if needed
ENV NIDOZO_DB=/data/nidozo.db

CMD ["uv", "run", "uvicorn", "nidozo.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "5001"]
