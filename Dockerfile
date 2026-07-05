# --- stage 1: build the React SPA -----------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- stage 2: python runtime -----------------------------------------
FROM python:3.11-slim
WORKDIR /app

# pandoc: DOCX export with native Word equations
# pango/cairo + fonts: weasyprint PDF engine
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    fonts-dejavu-core fonts-dejavu-extra \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY pdftransl/ pdftransl/
RUN pip install --no-cache-dir -e ".[pymupdf,export,backend,bot]" weasyprint

COPY backend/ backend/
COPY bot/ bot/
COPY --from=frontend /app/frontend/dist frontend/dist

ENV PDFTRANSL_DATA_DIR=/data \
    DJANGO_SETTINGS_MODULE=config.settings \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 600"]
