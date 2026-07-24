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
# Chromium for the PDF export path — renders KaTeX formulas (weasyprint
# can't run JS, so its PDFs would show raw LaTeX). --with-deps pulls the
# system libraries the headless browser needs.
RUN python -m playwright install --with-deps chromium

COPY backend/ backend/
COPY bot/ bot/
COPY --from=frontend /app/frontend/dist frontend/dist
# KaTeX dist for offline formula rendering in HTML/PDF exports
COPY --from=frontend /app/frontend/node_modules/katex/dist /app/vendor/katex

ENV PDFTRANSL_DATA_DIR=/data \
    DJANGO_SETTINGS_MODULE=config.settings \
    PDFTRANSL_KATEX_DIR=/app/vendor/katex \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Parsers and document converters process untrusted input.  Do not grant a
# compromise of one of those tools root access to the container or data volume.
RUN useradd --create-home --uid 10001 appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /app /data
COPY docker-entrypoint.sh /usr/local/bin/pdftransl-entrypoint
RUN chmod 755 /usr/local/bin/pdftransl-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/pdftransl-entrypoint"]
CMD ["sh", "-c", "python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-1} --timeout 600"]
