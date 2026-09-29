# ---------- Frontend build ----------
FROM node:22-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- Backend runtime ----------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdal-bin libgdal-dev libgeos-dev libproj-dev libnetcdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-core.txt -r requirements-live.txt -r requirements-report.txt

COPY backend/app ./backend/app
COPY backend/verify_oiltrace.py ./backend/verify_oiltrace.py
COPY models ./models
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

# M18: create the unprivileged runtime user; no runtime data is baked into
# the image (data/ is created at runtime via the mounted volume). The
# entrypoint starts as root only to repair a possibly stale/pre-existing
# data volume (root-owned from an older image), then drops to this user via
# setpriv before the server starts — the server itself never runs as root.
RUN useradd -m -u 10001 oiltrace && mkdir -p /app/data && chown -R oiltrace:oiltrace /app
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=8)"

CMD ["sh", "-c", "python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
