FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS application
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/insurance.db \
    STATIC_PATH=/app/frontend/dist \
    FIXTURES_PATH=/app/fixtures
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r /app/backend/requirements.lock \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data
COPY --chown=app:app backend/ /app/backend/
COPY --chown=app:app fixtures/ /app/fixtures/
COPY --chown=app:app scripts/ /app/scripts/
COPY --from=frontend-build --chown=app:app /build/frontend/dist /app/frontend/dist
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=3).read()"
CMD ["python", "scripts/start.py"]
