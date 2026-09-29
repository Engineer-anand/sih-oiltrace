#!/usr/bin/env bash
# Plan B start command for a NATIVE Render Python service.
# Identical to the Dockerfile CMD, but honours Render's injected $PORT.
set -e
exec python -m uvicorn app.main:app \
  --app-dir backend \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
